#!/usr/bin/env python3

import webbrowser
import json
from collections import defaultdict
from datetime import time
from html import escape


from sentence_transformers import SentenceTransformer, util

from liferay.teams.headless.headless_contstants import ComponentMapping
from utils.liferay_utils.jira_utils.jira_helpers import get_issue_status_by_key, get_issue_by_key, create_jira_task, get_all_issues, close_issue
from utils.liferay_utils.testray_utils.testray_api import *
from utils.liferay_utils.utilities import *

model = SentenceTransformer('all-MiniLM-L6-v2')

def handle_flaky_result(latest_build_id, jira_connection, result, case_id, result_error, epic):
    similar_open_issues = find_similar_open_issues(jira_connection, case_id, result_error, return_list=True)

    if similar_open_issues:
        print(f"Similar open issues: {similar_open_issues}")
        print(f"✔ Reassigning open issues {', '.join(similar_open_issues)} to result {result['id']}")
        return True, {
            "id": result["id"],
            "dueStatus": {"key": "TESTFIX", "name": "Test Fix"},
            "issues": ", ".join(similar_open_issues)
        }, None

    # Always create a new Test Fix task if none exist
    print("WE NEED TO CREATE SUBTASK: ")
    issue = create_testfix_task_for_subtask(
        case_id=case_id,
        latest_build_id=latest_build_id,
        jira_connection=jira_connection,
        epic=epic,
        result=result,
        result_error=result_error,
    )
    return True, {
        "id": result["id"],
        "dueStatus": {"key": "TESTFIX", "name": "Test Fix"},
        "issues": issue.key
    }, issue

def process_summary_result(
        summary_result,
        subtask_id,
        first_result,
        latest_build_id,
        jira_conn,
        epic,
        batch_updates,
        unique_tasks,
        case_ids,
        case_id_to_result,
        history_cache,
        task_id
):
    error = (summary_result.get("errors") or "")
    if should_skip_result(error):
        print(f"✔ Skipping result in subtask {subtask_id} due to known keyword")
        return True, None, False

    case_id = summary_result.get("r_caseToCaseResult_c_caseId")
    result_error = summary_result.get("errors", "")
    result_error_norm = normalize_error(result_error)

    case_id_to_result[case_id] = summary_result
    existing_issue = summary_result.get("issues")

    if first_result and should_skip_result(result_error):
        update_subtask_status(subtask_id)
        return True, None, False

    if existing_issue:
        return True, None, False

    if is_module_integration_test(case_id):
        is_flaky = False
    else:
        is_flaky = detect_flakiness(case_id, result_error_norm, history_cache)

    if is_flaky:
        handled, update_data, info = handle_flaky_result(
            latest_build_id, jira_conn, summary_result, case_id, result_error, epic
        )
        batch_updates.append(update_data)
        return True, None, False

    add_to_unique_tasks(unique_tasks, subtask_id, case_id, result_error)
    case_ids.add(case_id)
    return False, summary_result, True

def sort_cases_by_duration(subtask_case_pairs, case_duration_lookup):
    def safe_duration(c_id):
        d = case_duration_lookup.get(int(c_id))
        return d if isinstance(d, (int, float)) else float('inf')

    return sorted(subtask_case_pairs, key=lambda pair: safe_duration(pair[1]))


def build_case_rows(sorted_cases, case_duration_lookup, build_id, history_cache):
    printed_rows = []
    rca_info = None
    rca_batch = None
    rca_selector = None
    rca_compare = None

    failing_hash = get_current_build_hash(build_id)

    component_name = "Unknown"

    for _, case_id, component_id in sorted_cases:
        try:
            case_info = get_case_info(case_id)
            case_name = case_info.get("name", "N/A")
            case_type_id = case_info.get("r_caseTypeToCases_c_caseTypeId")
            case_type_name = get_case_type_name(case_type_id) if case_type_id else "Unknown"
            component_name = get_component_name(component_id) if component_id else "Unknown"
            raw_duration = case_duration_lookup.get(int(case_id))
            duration = raw_duration if isinstance(raw_duration, (int, float)) else None
            passing_hash = get_last_passing_git_hash(case_id, build_id, history_cache)
            github_compare = (
                f"https://github.com/liferay/liferay-portal/compare/{passing_hash}...{failing_hash}"
                if passing_hash and failing_hash else "###"
            )

            batch_name, test_selector = get_batch_info(case_name, case_type_name)

            if not rca_info and batch_name and test_selector:
                rca_info = build_rca_block(batch_name, test_selector, github_compare)
                rca_batch = batch_name
                rca_selector = test_selector
                rca_compare = github_compare

            elif not rca_info:
                rca_info = f"\nCompare: {github_compare}"

            row = [case_name, format_duration(duration), component_name]
            printed_rows.append(row)

        except Exception as e:
            print(f"[ERROR] Failed to fetch data for case_id={case_id} → {e}")

    return printed_rows, rca_info, rca_batch, rca_selector, rca_compare, component_name


def get_batch_info(case_name, case_type_name):
    if case_type_name == "Playwright Test":
        selector = case_name.split(" >")[0] if " >" in case_name else case_name
        return "playwright-js-tomcat101-postgresql163", selector
    elif case_type_name == "Automated Functional Test":
        return "functional-tomcat101-postgresql163", case_name
    elif case_type_name == "Modules Integration Test":
        trimmed_name = case_name.split(".")[-1]
        return "modules-integration-postgresql163", f"\\*\\*/src/testIntegration/\\*\\*/{trimmed_name}.java"
    return None, None


def build_rca_block(batch_name, test_selector, github_compare):
    return (
        "\nParameters to run Root Cause Analysis on https://test-1-1.liferay.com/job/root-cause-analysis-tool/ :\n"
        f"PORTAL_BATCH_NAME: {batch_name}\n"
        f"PORTAL_BATCH_TEST_SELECTOR: {test_selector}\n"
        f"PORTAL_BRANCH_SHAS: {github_compare}\n"
        f"PORTAL_GITHUB_URL: https://github.com/liferay/liferay-portal/tree/master\n"
        f"PORTAL_UPSTREAM_BRANCH_NAME: master"
    )

def build_rca_html_block(batch_name, test_selector, github_compare):
    return (
            "<p>Parameters to run "
            "<a href='https://test-1-1.liferay.com/job/root-cause-analysis-tool/' target='_blank'>"
            "Root Cause Analysis Tool</a>:</p>"
            "<pre><b>PORTAL_BATCH_NAME</b>: " + batch_name + "\n"
                                                             "<b>PORTAL_BATCH_TEST_SELECTOR</b>: " + test_selector + "\n"
                                                                                                                     "<b>PORTAL_BRANCH_SHAS</b>: <a href='" + github_compare + "' target='_blank'>" + github_compare + "</a>\n"
                                                                                                                                                                                                                       "<b>PORTAL_GITHUB_URL</b>: <a href='https://github.com/liferay/liferay-portal/tree/master' target='_blank'>"
                                                                                                                                                                                                                       "https://github.com/liferay/liferay-portal/tree/master</a>\n"
                                                                                                                                                                                                                       "<b>PORTAL_UPSTREAM_BRANCH_NAME</b>: master</pre>"
    )

def get_build_from_beginning_of_current_quarter(builds):
    quarter_start_date, _, _ = get_current_quarter_info()  # unpack only the first value (date)
    quarter_start = datetime.combine(quarter_start_date, time.min)  # convert to datetime at 00:00:00

    best_build = None
    best_delta = None

    for build in builds:
        due_str = build.get("dueDate")
        if not due_str:
            continue

        dt = parse_execution_date(due_str)
        if not dt or dt < quarter_start:
            continue

        delta = dt - quarter_start
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_build = build

    return best_build["id"] if best_build else None


def get_latest_done_build(builds):
    latest_build = builds[0]
    if latest_build.get("importStatus", {}).get("key") != "DONE":
        print(f"✘ Latest build '{latest_build.get('name')}' is not DONE.")
        return None
    return latest_build


def find_or_create_task(build, jira_connection, latest_build_id):
    build_to_tasks = get_build_tasks(build["id"])

    if not build_to_tasks:
        print(f"[CREATE] No tasks for build '{build['name']}', creating task and testflow.")
        task = create_task(build)
        create_testflow(task["id"])
        return task["id"]

    for task in build_to_tasks:
        due_status_key = task.get("dueStatus", {}).get("key")
        if due_status_key == "ABANDONED":
            print(f"Task {task['id']} has been ABANDONED.")
            return None
        print(f"[USE] Using existing task {task['id']} with status {due_status_key}.")
        task_id = task["id"]
        subtasks = get_task_subtasks(task_id)
        if check_and_complete_task_if_all_subtasks_done(task_id,subtasks,jira_connection, latest_build_id):
            return None
        return task_id
    return None


def get_latest_build_with_completed_task(builds):
    for build in builds:
        build_tasks = get_build_tasks(build["id"])
        for task in build_tasks:
            if task.get("dueStatus", {}).get("key") == "COMPLETE":
                return build
    return None

def is_handled(result):
    err = (result.get("errors") or "")
    return bool(result.get("issues")) or should_skip_result(err)

def should_skip_result(error):
    skip_error_keywords = [
        "Failed prior to running test",
        "PortalLogAssertorTest#testScanXMLLog",
        "Skipped test",
        "The build failed prior to running the test",
        "test-portal-testsuite-upstream-downstream(master) timed out after",
        "TEST_SETUP_ERROR",
        "Unable to run test on CI"
    ]
    return any(keyword in error for keyword in skip_error_keywords)


def build_flaky_result_metadata(latest_build_id,result, case_id, result_error):
    case_info = get_case_info(case_id)
    return {
        "link": f"https://testray.liferay.com/web/testray#/project/35392/routines/{HEADLESS_ROUTINE_ID}/build/{latest_build_id}/case-result/{result['id']}",
        "test_name": case_info.get("name", "N/A"),
        "error": result_error,
        "component": get_component_name(case_info.get("r_componentToCases_c_componentId")) or "Unknown"
    }

def create_testfix_task_for_subtask(
        case_id,
        latest_build_id,
        jira_connection,
        epic,
        result,
        result_error
):
    """
    Creates a Test Fix task in Jira when a flaky result is detected and
    no similar open issues exist. It includes context about the failing case,
    component, and error for quicker triage.
    """

    case_info = get_case_info(case_id)
    component_name = get_component_name(case_info.get("r_componentToCases_c_componentId")) or "Unknown"

    # Build description
    description_lines = [
        "*Flaky Test Detected in Testray*",
        f"[Testray Result|https://testray.liferay.com/web/testray#/project/35392/routines/{HEADLESS_ROUTINE_ID}/build/{latest_build_id}/case-result/{result['id']}]",
        "",
        "h3. Error",
        f"{{code}}{result_error}{{code}}",
        "",
        "h3. Test Details",
        f"*Name:* {case_info.get('name', 'N/A')}",
        f"*Component:* {component_name}",
        f"*Result ID:* {result['id']}",
    ]

    summary = f"Test Fix: {case_info.get('name', 'N/A')} - {result_error[:80]}"
    description = "\n".join(description_lines)

    jira_components = [
        ComponentMapping.TestrayToJira.get(component_name, component_name)
    ]

    # Create Jira issue
    issue = create_jira_task(
        jira_local=jira_connection,
        epic=epic,
        summary=summary,
        description=description,
        component=jira_components,
        label="test_fix"
    )

    print(f"✔ Created Test Fix task for case {case_id}: {issue.key}")

    return issue

def find_similar_open_issues(jira_connection, case_id, result_error, *, return_list=False):
    """
    Look for similar errors in history that have open Jira issues.

    Args:
        jira_connection: Authenticated Jira connection
        case_id: Identifier to retrieve test history
        result_error: The current error message
        return_list: If True, return list of issue keys; if False, return BLOCKED dict

    Returns:
        If return_list=True:
            List of matching open issue keys (or empty list if none)
        Else:
            Tuple[bool, dict or None] - like the original 'for_unique' function
    """
    seen_issues = set()
    similar_open_issues = []

    history = get_case_result_history_for_routine_not_passed(case_id)
    result_error_norm = normalize_error(result_error)

    for past_result in history:
        issues_str = past_result.get("issues", "")
        if not issues_str:
            continue

        issue_keys = [key.strip() for key in issues_str.split(",")]
        open_issues = []

        for issue_key in issue_keys:
            if issue_key in seen_issues:
                continue
            try:
                _, status = get_issue_status_by_key(jira_connection, issue_key)
                if status != "Closed":
                    open_issues.append(issue_key)
                seen_issues.add(issue_key)
            except Exception as e:
                print(f"Error retrieving issue {issue_key}: {e}")

        if not open_issues:
            continue

        history_error = past_result.get("error", "")
        history_error_norm = normalize_error(history_error)

        if are_errors_similar(result_error_norm, history_error_norm):
            if return_list:
                similar_open_issues.extend(open_issues)
                return similar_open_issues  # stop at first
            else:
                return True, {
                    "dueStatus": {"key": "BLOCKED", "name": "Blocked"},
                    "issues": ", ".join(open_issues)
                }

    return similar_open_issues if return_list else (False, None)


def handle_module_integration_flaky(latest_build_id,result, case_id, result_error, similar_open_issues):
    print(f"📌 Case ID {case_id} is a Modules Integration Test. Evaluate manually if it is flaky.")
    error_link = get_error_messages_link(result)
    if error_link:
        print(f"🔗 Error Messages Link: {error_link}")
    if similar_open_issues:
        print(f"🛠 Similar open LPD issues found: {', '.join(similar_open_issues)}")

    return False, None, build_flaky_result_metadata(latest_build_id,result, case_id, result_error)

def add_to_unique_tasks(unique_tasks, subtask_id, case_id, error):
    unique_tasks.append({
        "subtask_id": subtask_id,
        "case_id": case_id,
        "error": error
    })


def detect_flakiness(case_id, current_error_norm, history_cache):
    history = history_cache.get(case_id)
    if history is None:
        history = get_case_result_history_for_routine(case_id)
        history_cache[case_id] = history

    if not history:
        return False, "no_history"

    fail_count = 0
    pass_count = 0
    switch_count = 0
    last_status = None
    similar_error_failures = 0
    unique_errors = set()

    for idx, result in enumerate(history):
        status = result.get("status")
        raw_error = result.get("error", "")
        error = normalize_error(raw_error)

        if status in STATUS_FAILED_BLOCKED_TESTFIX:
            fail_count += 1
            unique_errors.add(error)
            is_similar = are_errors_similar(current_error_norm, error)
            if is_similar:
                similar_error_failures += 1
        elif status == "PASSED":
            pass_count += 1

        if last_status and last_status != status:
            switch_count += 1
        last_status = status

    total = pass_count + fail_count

    if total < 5:
        return False, "insufficient_data"

    flakiness_score = switch_count / (total - 1)
    failure_rate = fail_count / total
    similar_error_ratio = similar_error_failures / fail_count

    is_flaky = (
            flakiness_score > 0.12 and
            0.05 < failure_rate < 0.75 and
            similar_error_ratio >= 0.9
    )

    return is_flaky


def report_poshi_tests_decrease(start_of_quarter_count, current_count):
    if start_of_quarter_count == 0:
        print("Cannot calculate decrease percentage (division by zero).")
        return

    items_less = start_of_quarter_count - current_count
    decrease_percent = (items_less / start_of_quarter_count) * 100

    if decrease_percent < 10.0:
        print(f"The total number of POSHI tests has gone down by {decrease_percent:.2f}% "
              f"compared to what it was at the beginning of the quarter. "
              f"We're targeting a 10% decrease, so there's still work to do.")
    else:
        print(f"The total number of POSHI tests has gone down by {decrease_percent:.2f}% "
              f"compared to what it was at the beginning of the quarter. "
              f"KPI of 10% accomplished, but keep pushing!")


def count_automated_functional_cases(all_cases_info):
    automated_count = 0
    case_type_cache = {}

    for item in all_cases_info:
        case = item.get("r_caseToCaseResult_c_case")
        if not case:
            continue
        case_type_id = case.get("r_caseTypeToCases_c_caseTypeId")
        if not case_type_id:
            continue

        if case_type_id not in case_type_cache:
            case_type_cache[case_type_id] = get_case_type_name(case_type_id)

        if case_type_cache[case_type_id] == "Automated Functional Test":
            automated_count += 1

    return automated_count


def is_automated_functional_test(c_id):
    case_info = get_case_info(c_id)
    case_type_id = case_info.get("r_caseTypeToCases_c_caseTypeId")
    return get_case_type_name(case_type_id) == "Automated Functional Test"


def is_module_integration_test(c_id):
    case_info = get_case_info(c_id)
    case_type_id = case_info.get("r_caseTypeToCases_c_caseTypeId")
    return get_case_type_name(case_type_id) == "Modules Integration Test"


def check_and_complete_task_if_all_subtasks_done(task_id, subtasks, jira_connection, latest_build_id):
    all_complete = all(subtask.get("dueStatus", {}).get("key") == "COMPLETE" for subtask in subtasks)

    if all_complete:
        # Collect all unique issue keys from the completed subtasks
        testray_issue_keys = set()
        for subtask in subtasks:
            issues_str = subtask.get("issues", "")
            if issues_str:
                testray_issue_keys.update(key.strip() for key in issues_str.split(','))

        # Fetch open routine tasks from Jira
        jql = "labels in ('hl_routine_tasks') AND labels not in ('test_fix') AND status = Open"
        open_jira_issues = get_all_issues(jira_connection, jql, fields=["key"])
        open_jira_issue_keys = {issue.key for issue in open_jira_issues}

        # Find issues that are open in Jira but not present in our completed TestRay task
        issues_to_close = open_jira_issue_keys - testray_issue_keys

        if issues_to_close:
            build_hash = get_current_build_hash(latest_build_id)
            print(f"ℹ Found {len(issues_to_close)} issues to close as they are not reproducible in this run.")
            for issue_key in issues_to_close:
                close_issue(jira_connection, issue_key, build_hash)

        print(f"✔ All subtasks are complete, completing task {task_id}")
        complete_task(task_id)
        return True  # Task completed
    else:
        print(f"ℹ Not all subtasks are complete for task {task_id}, task remains in progress")
        return False  # Task still open

def are_errors_similar(current_norm, history_norm, threshold=0.8):
    """
    Compare two error messages semantically using sentence embeddings.
    """
    emb_a = model.encode(current_norm, convert_to_tensor=True)
    emb_b = model.encode(history_norm, convert_to_tensor=True)
    similarity = util.pytorch_cos_sim(emb_a, emb_b).item()

    return similarity >= threshold

#def are_errors_similar(current, history, threshold=0.6):
#    current_norm = normalize_error(current)
#    history_norm = normalize_error(history)
#
#    similarity = jellyfish.jaro_winkler_similarity(current_norm, history_norm)
#
#    if "testGetObjectEntryWithKeywords" in current:
#        print("Current norm:", current_norm)
#        print("History norm:", history_norm)
#        print("Similarity between them:", str(similarity))
#
#    return similarity >= threshold

def group_errors_by_type(unique_tasks):
    error_to_cases = defaultdict(list)
    for item in unique_tasks:
        error_to_cases[item["error"]].append((item["subtask_id"], item["case_id"], item["component_id"]))
    return error_to_cases


def build_case_duration_lookup(unique_tasks, build_id):
    raw_build_results = get_all_build_case_results(build_id)
    interested_case_ids = {
        int(item["case_id"]) for item in unique_tasks if item.get("case_id")
    }

    return {
        int(item["r_caseToCaseResult_c_caseId"]): item.get("duration")
        for item in raw_build_results
        if item.get("r_caseToCaseResult_c_caseId")
           and int(item["r_caseToCaseResult_c_caseId"]) in interested_case_ids
    }


def get_error_messages_link(result_summary):
    case_result_id = result_summary.get("id")
    if not case_result_id:
        print("Warning: No 'id' found in result_summary")

    failure_url = fetch_failure_url(case_result_id)
    if not failure_url:
        print("No failure message URL found in attachments.")
    return failure_url


def is_flagged_as_flaky(case_id):
    info = get_case_info(case_id)
    flaky_flag = info.get('flaky', None)

    if flaky_flag is None:
        print(f"[WARNING] Flaky flag doesn't exist for case_id={case_id}")

    return flaky_flag or False


def fetch_failure_url(case_result_id):
    detailed_result = get_case_result(case_result_id)
    attachments_json_str = detailed_result.get('attachments', '[]')

    try:
        attachments = json.loads(attachments_json_str)
        for attachment in attachments:
            if attachment.get('name') == "Failure Messages":
                return attachment.get('url')
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse attachments JSON: {e}")

    return None


def get_case_result_history_for_routine(case_id):
    items = fetch_case_results(case_id, HEADLESS_ROUTINE_ID)
    return sort_by_execution_date_desc(items)


def get_case_result_history_for_routine_not_passed(case_id):
    items = fetch_case_results(case_id, HEADLESS_ROUTINE_ID, status=STATUS_FAILED_BLOCKED_TESTFIX)
    return sort_by_execution_date_desc(items)


def sort_by_execution_date_desc(items):
    def get_sort_key(item):
        date_str = item.get("executionDate", "")
        parsed_date = parse_execution_date(date_str)
        return parsed_date or datetime.min

    return sorted(items, key=get_sort_key, reverse=True)


def get_last_passing_result(entire_history, max_execution_date):
    status_passed = "PASSED"

    if isinstance(max_execution_date, str):
        max_execution_date = parse_execution_date(max_execution_date)
        if not max_execution_date:
            print("❌ Invalid max_due_date format")
            return None

    last_passing = None
    last_date = None

    for item in entire_history:
        if item.get("status") != status_passed:
            continue

        execution_date_str = item.get("executionDate")
        if not execution_date_str:
            continue

        execution_date = parse_execution_date(execution_date_str)
        if not execution_date or execution_date >= max_execution_date:
            continue

        if last_date is None or execution_date > last_date:
            last_passing = item
            last_date = execution_date

    return last_passing


def filter_case_result_history_by_build(history, build_id):
    """Filter case result history by build ID."""
    return [
        item for item in history
        if item.get("testrayBuildId") == build_id
    ]


def get_last_passing_git_hash(case_id, build_id, history_cache):
    entire_history = history_cache.get(case_id)
    if entire_history is None:
        entire_history = get_case_result_history_for_routine(case_id)
        history_cache[case_id] = entire_history

    result_history_for_build = filter_case_result_history_by_build(entire_history, build_id)
    if not result_history_for_build:
        return None

    failing_hash_execution_date = result_history_for_build[0].get('executionDate')
    item = get_last_passing_result(entire_history, failing_hash_execution_date)
    last_passing_hash = item.get('gitHash') if item else None
    return last_passing_hash


def get_current_build_hash(build_id):
    build = get_build_info(build_id)
    git_hash = build.get('gitHash')
    return git_hash


def get_task_routine_id(task_id):
    build_id = get_task_build_id(task_id)
    build_info = get_build_info(build_id)
    return build_info["r_routineToBuilds_c_routineId"]

def create_investigation_task_for_subtask(
        subtask_unique_failures,
        subtask_id,
        latest_build_id,
        jira_connection,
        epic,
        task_id,
        case_history_cache
):
    """
    Creates an investigation task in Jira for a subtask with unique failures.
    Groups failures by error (like in generate_combined_html_report),
    outputs a Jira-friendly description with a table of test names, components, duration,
    and RCA details (once, from first test).
    Updates Testray results with BLOCKED status and Jira issue key.
    """

    # Group by error
    error_to_cases = group_errors_by_type(subtask_unique_failures)
    case_duration_lookup = build_case_duration_lookup(subtask_unique_failures, latest_build_id)

    description_lines = [
        "*Unique Failures in Testray Subtask*",
        f"[Testray Subtask|https://testray.liferay.com/web/testray#/testflow/{task_id}/subtasks/{subtask_id}]",
        "",
    ]

    first_error = None
    rca_included = False

    component_name = None

    for error, subtask_case_pairs in error_to_cases.items():
        if not first_error:
            first_error = error[:80]  # for Jira summary

        description_lines.append("h3. Error")  # heading
        description_lines.append(f"{{code}}{error}{{code}}")  # code block for error

        sorted_cases = sort_cases_by_duration(subtask_case_pairs, case_duration_lookup)
        printed_rows, rca_info, batch_name, test_selector, github_compare, component_name = build_case_rows(
            sorted_cases, case_duration_lookup, latest_build_id, case_history_cache
        )

        description_lines.append("")
        description_lines.append("|| Test Name || Component || Duration ||")  # table header
        for row in printed_rows:
            name, duration, component = row
            description_lines.append(f"| {name} | {component} | {duration} |")  # table rows

        # Blank line
        description_lines.append("")
        # Include RCA info once, from the first test of the first error
        if not rca_included and batch_name and test_selector and github_compare:
            description_lines.append("")
            description_lines.append("h3. RCA Details")
            description_lines.append("")
            description_lines.append(f"*Batch:* {batch_name}")
            description_lines.append(f"*Test Selector:* {test_selector}")
            description_lines.append(f"*GitHub Compare:* {github_compare}")
            rca_included = True

    summary = f"Investigate {first_error}..."
    description = "\n".join(description_lines)
    jira_components = [
        ComponentMapping.TestrayToJira.get(c, c)  # fallback to original if not mapped
        for c in component_name.split(",")
    ]

    # Create Jira issue
    issue = create_jira_task(
        jira_local=jira_connection,
        epic=epic,
        summary=summary,
        description=description,
        component=jira_components,
        label=None
    )

    print(f"✔ Created investigation task for subtask {subtask_id}: {issue.key}")

    return issue