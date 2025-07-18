#!/usr/bin/env python3

import webbrowser
import json
from collections import defaultdict
from datetime import time
from html import escape

import jellyfish

from liferay.teams.headless.headless_contstants import ComponentMapping
from utils.liferay_utils.jira_utils.jira_helpers import get_issue_status_by_key, create_task_for_flaky_test
from utils.liferay_utils.testray_utils.testray_api import *
from utils.liferay_utils.utilities import *

def process_summary_result(
        summary_result,
        subtask_id,
        summary_results,
        first_result,
        latest_build_id,
        jira_conn,
        epic,
        batch_updates,
        unique_tasks,
        case_ids,
        case_id_to_result,
        total_flaky_without_issue,
):
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

    is_flaky = detect_flakiness(case_id, result_error_norm)

    if is_flaky:
        handled, update_data, info = handle_flaky_result(
            latest_build_id, jira_conn, summary_result, case_id, result_error
        )

        if handled:
            batch_updates.append(update_data)
            return True, None, False

        # Flaky but not handled
        total_flaky_without_issue.append(info)

        test_name = info.get("test_name")
        testray_component = info.get("component")
        link = info.get("link")
        error = info.get("error")

        jira_component = ComponentMapping.TestrayToJira.get(testray_component)

        if len(summary_results) == 1:
            is_automated = is_automated_functional_test(case_id)

            if is_automated and jira_component == "Objects > Object Entries REST APIs":
                summary = "[move to integration]" + test_name
            elif is_automated:
                summary = "[move to playwright] " + test_name
            else:
                summary = "[test-fix] " + test_name

            description = (
                "*Testray Result*\n"
                f"[Testray result link|{link}]\n\n"
                "*Error Details:*\n"
                "{code}\n"
                f"{error}\n"
                "{code}\n"
            )

            if jira_component:
                created_issue = create_task_for_flaky_test(epic, jira_component, summary, description, jira_conn)
                print(f"Issue Key: {created_issue.key}")
                update_data = {
                    "id": summary_result.get("id"),
                    "dueStatus": {"key": "TESTFIX", "name": "Test Fix"},
                    "issues": created_issue.key,
                }
                batch_updates.append(update_data)
            else:
                print(f"⚠ Testray component '{testray_component}' has no mapped JIRA component.")

        # Skip unique processing for flaky tests
        return False, info, False

    else:
        is_open_unique = handle_unique_result(
            jira_conn,
            summary_result,
            subtask_id,
            case_id,
            result_error,
            unique_tasks,
            case_ids,
            batch_updates,
        )
        return False, None, is_open_unique

def print_error_header(error, subtask_case_pairs, task_id, case_id_to_result):
    first_subtask_id = subtask_case_pairs[0][0]
    subtask_url = f"https://testray.liferay.com/web/testray#/testflow/{task_id}/subtasks/{first_subtask_id}"
    print(f"\n🔗 {subtask_url}")
    print(f"💨 Error:\n{error}\n")

    first_case_id = subtask_case_pairs[0][1]
    if is_module_integration_test(first_case_id):
        result = case_id_to_result.get(first_case_id)
        error_link = get_error_messages_link(result) if result else None
        if error_link:
            print(f"🔗 Error Messages Link: {error_link}\n")


def sort_cases_by_duration(subtask_case_pairs, case_duration_lookup):
    def safe_duration(c_id):
        d = case_duration_lookup.get(int(c_id))
        return d if isinstance(d, (int, float)) else float('inf')

    return sorted(subtask_case_pairs, key=lambda pair: safe_duration(pair[1]))


def build_case_rows(sorted_cases, case_duration_lookup, build_id):
    printed_rows = []
    rca_info = None
    batch_name = None
    test_selector = None
    github_compare = None

    header = f"{'Case Name':<150} {'Duration':<15} {'Component Name':<30}"
    #print(header)
    #print("-" * (len(header) + 5))

    for _, case_id in sorted_cases:
        try:
            if is_flagged_as_flaky(case_id):
                continue

            case_info = get_case_info(case_id)
            case_name = case_info.get("name", "N/A")
            component_id = case_info.get("r_componentToCases_c_componentId")
            component_name = get_component_name(component_id) if component_id else "Unknown"
            case_type_id = case_info.get("r_caseTypeToCases_c_caseTypeId")
            case_type_name = get_case_type_name(case_type_id) if case_type_id else "Unknown"
            raw_duration = case_duration_lookup.get(int(case_id))
            duration = raw_duration if isinstance(raw_duration, (int, float)) else None
            failing_hash, passing_hash = get_last_passing_first_failing_git_hash(case_id, build_id)
            github_compare = (
                f"https://github.com/liferay/liferay-portal/compare/{passing_hash}...{failing_hash}"
                if passing_hash and failing_hash else "###"
            )

            batch_name, test_selector = get_batch_info(case_name, case_type_name)

            if not rca_info and batch_name and test_selector:
                rca_info = build_rca_block(batch_name, test_selector, github_compare)
            elif not rca_info:
                rca_info = f"\nCompare: {github_compare}"

            row = [case_name, format_duration(duration), component_name]
            printed_rows.append(row)

        except Exception as e:
            print(f"[ERROR] Failed to fetch data for case_id={case_id} → {e}")

    return printed_rows, rca_info, batch_name, test_selector, github_compare


def get_batch_info(case_name, case_type_name):
    if case_type_name == "Playwright Test":
        selector = case_name.split(" >")[0] if " >" in case_name else case_name
        return "playwright-js-tomcat101-postgresql163", selector
    elif case_type_name == "Automated Functional Test":
        return "functional-tomcat101-postgresql163", case_name
    elif case_type_name == "Modules Integration Test":
        trimmed_name = case_name.split(".")[-1]
        return "modules-integration-postgresql163", f"**/src/testIntegration/**/{trimmed_name}.java"
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


def find_or_create_task(build):
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
        if check_and_complete_task_if_all_subtasks_done(task_id):
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

def find_similar_open_issue_for_unique(jira_connection, case_id, result_error):
    """Look for similar errors in history with open Jira issues."""
    history = get_case_result_history_for_routine_not_passed(case_id)

    seen_issues = set()

    for past_result in history:
        history_error_norm = normalize_error(past_result.get("error", ""))
        result_error_norm = normalize_error(result_error)

        if not are_errors_similar(result_error_norm, history_error_norm):
            continue

        issues_str = past_result.get("issues", "")
        if not issues_str:
            continue

        issue_keys = [key.strip() for key in issues_str.split(",")]

        for issue_key in issue_keys:
            if issue_key in seen_issues:
                continue

            try:
                issue, status = get_issue_status_by_key(jira_connection, issue_key)
                if status != "Closed":
                    return True, {
                        "dueStatus": {"key": "BLOCKED", "name": "Blocked"},
                        "issues": issue_key
                    }
                seen_issues.add(issue_key)
            except Exception as e:
                print(f"Error retrieving issue {issue_key}: {e}")
    return False, None


def handle_flaky_result(latest_build_id,jira_connection, result, case_id, result_error):
    history = get_case_result_history_for_routine_not_passed(case_id)

    similar_open_issues = find_similar_open_issues(jira_connection, history, result_error)

    if is_module_integration_test(case_id):
        return handle_module_integration_flaky(latest_build_id,result, case_id, result_error, similar_open_issues)

    if similar_open_issues:
        print(f"Similar open issues: {similar_open_issues}")
        print(f"✔ Reassigning open issues {', '.join(similar_open_issues)} to result {result['id']}")
        return True, {
            "id": result["id"],
            "dueStatus": {"key": "TESTFIX", "name": "Test Fix"},
            "issues": ", ".join(similar_open_issues)
        }, None

    return False, None, build_flaky_result_metadata(latest_build_id,result, case_id, result_error)


def find_similar_open_issues(jira_connection, history, result_error):
    seen_issues = set()
    similar_open_issues = []

    for past_result in history:
        result_error_norm = normalize_error(result_error)
        history_error_norm = normalize_error(past_result.get("error", ""))

        if not are_errors_similar(result_error_norm, history_error_norm):
            continue

        issues_str = past_result.get("issues", "")
        if not issues_str:
            continue

        print(f"{issues_str}")
        issue_keys = [key.strip() for key in issues_str.split(",")]

        for issue_key in issue_keys:
            if issue_key in seen_issues:
                continue

            try:
                issue, status = get_issue_status_by_key(jira_connection, issue_key)
                if status != "Closed":
                    similar_open_issues.append(issue_key)
                    seen_issues.add(issue_key)
                    # Stop at first open issue
                    return similar_open_issues
            except Exception as e:
                print(f"Error retrieving issue {issue_key}: {e}")

    return similar_open_issues


def handle_module_integration_flaky(latest_build_id,result, case_id, result_error, similar_open_issues):
    print(f"📌 Case ID {case_id} is a Modules Integration Test. Evaluate manually if it is flaky.")
    error_link = get_error_messages_link(result)
    if error_link:
        print(f"🔗 Error Messages Link: {error_link}")
    if similar_open_issues:
        print(f"🛠 Similar open LPD issues found: {', '.join(similar_open_issues)}")

    return False, None, build_flaky_result_metadata(latest_build_id,result, case_id, result_error)


def handle_unique_result(
        jira_connection,
        result,
        subtask_id,
        case_id,
        result_error,
        unique_tasks,
        case_ids,
        batch_updates
):
    add_to_unique_tasks(unique_tasks, subtask_id, case_id, result_error)
    case_ids.add(case_id)

    return process_unique_result(jira_connection, result, case_id, result_error, batch_updates)


def add_to_unique_tasks(unique_tasks, subtask_id, case_id, error):
    unique_tasks.append({
        "subtask_id": subtask_id,
        "case_id": case_id,
        "error": error
    })


def process_unique_result(jira_connection, result, case_id, result_error, batch_updates):
    found, update_data = find_similar_open_issue_for_unique(jira_connection, case_id, result_error)

    if found:
        update_data["id"] = result["id"]
        batch_updates.append(update_data)
        print(f"✔ Marking unique result {result['id']} as BLOCKED due to similar open issue {update_data['issues']}")
        return True

    return False


def detect_flakiness(case_id, current_error_norm):
    history = get_case_result_history_for_routine(case_id)

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


def check_and_complete_task_if_all_subtasks_done(task_id):
    subtasks = get_task_subtasks(task_id)
    all_complete = all(subtask.get("dueStatus", {}).get("key") == "COMPLETE" for subtask in subtasks)

    if all_complete:
        print(f"✔ All subtasks are complete, completing task {task_id}")
        complete_task(task_id)
        return True  # Task completed
    else:
        print(f"ℹ Not all subtasks are complete for task {task_id}, task remains in progress")
        return False  # Task still open


def print_summary_report(total_reused, total_unique, total_flaky_without_issue):
    print("\n--- Summary Report ---")
    print(f"Total issues reassigned from previous builds: {total_reused}")
    print(f"Total unique tasks: {total_unique}")
    print(f"Total flaky tests without open Jira issue: {len(total_flaky_without_issue)}")


def are_errors_similar(current, history, threshold=0.6):
    current_norm = normalize_error(current)
    history_norm = normalize_error(history)
    similarity = jellyfish.jaro_winkler_similarity(current_norm, history_norm)
    return similarity >= threshold

def print_flaky_tests_without_issues(flaky_tests):
    print("\n--- Flaky Tests Without Jira Issue ---")
    for flaky in flaky_tests:
        print(f"\nLink: {flaky['link']}")
        print(f"Test Name: {flaky['test_name']}")
        print(f"Error: {flaky['error']}")
        print(f"Component: {flaky['component']}")
        print("Suggestion: Consider creating a Jira ticket with this information.")

def print_grouped_unique_errors(unique_tasks, task_id, case_id_to_result, build_id):
    print("\n--- Grouped Unique Errors ---\n")

    error_to_cases = group_errors_by_type(unique_tasks)
    case_duration_lookup = build_case_duration_lookup(unique_tasks, build_id)

    for error, subtask_case_pairs in error_to_cases.items():
        print_error_header(error, subtask_case_pairs, task_id, case_id_to_result)

        sorted_cases = sort_cases_by_duration(subtask_case_pairs, case_duration_lookup)

        printed_rows, rca_info, batch_name, test_selector, github_compare = build_case_rows(
            sorted_cases, case_duration_lookup, build_id
        )

        for row in printed_rows:
            print(row)

        if rca_info:
            print(rca_info)


def group_errors_by_type(unique_tasks):
    error_to_cases = defaultdict(list)
    for item in unique_tasks:
        error_to_cases[item["error"]].append((item["subtask_id"], item["case_id"]))
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


def get_last_passing_first_failing_git_hash(case_id, build_id):
    first_failing_hash = get_current_build_hash(build_id)
    entire_history = get_case_result_history_for_routine(case_id)
    result_history_for_build = filter_case_result_history_by_build(entire_history, build_id)
    failing_hash_execution_date = result_history_for_build[0].get('executionDate')
    item = get_last_passing_result(entire_history, failing_hash_execution_date)
    last_passing_hash = item.get('gitHash')
    return first_failing_hash, last_passing_hash


def get_current_build_hash(build_id):
    build = get_build_info(build_id)
    git_hash = build.get('gitHash')
    return git_hash


def get_task_routine_id(task_id):
    build_id = get_task_build_id(task_id)
    build_info = get_build_info(build_id)
    return build_info["r_routineToBuilds_c_routineId"]


def generate_combined_html_report(flaky_tests, unique_tasks, task_id, case_id_to_result, build_id, output_path="combined_report.html"):
    html = [
        "<html>",
        "<head><meta charset='UTF-8'><title>Testray Flaky and Unique Failures Report</title>",
        "<style>",

        # --- GLOBAL LAYOUT & THEMING ---
        "body { font-family: Arial, sans-serif; padding: 20px; background-color: #f9f9f9; }",
        ".grid { display: grid; grid-template-columns: 1fr; gap: 30px; }",

        # --- MAIN CARDS (wrappers) ---
        ".card { background-color: #ffffff; border-left: 8px solid #0b5fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1); }",
        ".card.flaky { border-left-color: #2e6c80; }",
        ".card.unique { border-left-color: #0b5fff; }",

        # --- SUBCARDS (inside cards) ---
        ".subcard { background-color: #f9fbff; border: 1px solid #d3e3fd; border-radius: 6px; padding: 15px; margin-top: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }",
        ".subcard.flaky { border-left: 4px solid #2e6c80; }",
        ".subcard.unique { border-left: 4px solid #0b5fff; }",

        # --- HEADINGS ---
        "h2, h3 { margin-top: 0; }",
        "h2.flaky-title { color: #2e6c80; }",
        "h2.unique-title { color: #0b5fff; }",

        # --- TABLES ---
        "table { border-collapse: collapse; width: 100%; margin-top: 10px; }",
        "th { background-color: #e6f0ff; color: #0b5fff; }",
        "td, th { border: 1px solid #ccc; padding: 8px; text-align: left; }",
        "tr:nth-child(even) { background-color: #f4faff; }",
        "tr:hover { background-color: #edf4fb; }",

        # --- CODE BLOCKS & ERROR SNIPPETS ---
        "code { background-color: #eef5fa; padding: 2px 4px; border-radius: 3px; font-size: 90%; }",
        "pre { background-color: #f4faff; padding: 10px; border-left: 4px solid #50c8b0; border-radius: 4px; }",

        # --- LINKS ---
        "a { color: #0b5fff; text-decoration: none; }",
        "a:hover { text-decoration: underline; }",

        "</style>",
        "</head>",
        "<body>",
        "<div class='grid'>",  # Grid layout wrapper

        # ✅ Only one Flaky card starts here:
        "<div class='card flaky'>",
        "<h2 class='flaky-title'>Flaky Tests Without Jira Issue</h2>"
    ]

    if flaky_tests:
        for flaky in flaky_tests:
            html.append("<div class='subcard flaky'>")
            html.append("<table><tr><th>Test Name</th><th>Error</th><th>Component</th><th>Link</th></tr>")
            html.append(
                f"<tr><td>{escape(flaky['test_name'])}</td>"
                f"<td><code>{escape(flaky['error'])}</code></td>"
                f"<td>{escape(flaky['component'])}</td>"
                f"<td><a href='{flaky['link']}' target='_blank'>View Result</a></td></tr>"
            )
            html.append("</table>")
            html.append("</div>")  # End subcard
    else:
        html.append("<p>No flaky tests without Jira issue found.</p>")

    html.append("</div>")  # End Flaky Card


    # Grouped Unique Errors Section
    html.append("<div class='card unique'>")
    html.append("<h2 class='unique-title'>Grouped Unique Errors</h2>")

    # Build case blocks per unique error
    error_to_cases = group_errors_by_type(unique_tasks)
    case_duration_lookup = build_case_duration_lookup(unique_tasks, build_id)

    for error, subtask_case_pairs in error_to_cases.items():
        sorted_cases = sort_cases_by_duration(subtask_case_pairs, case_duration_lookup)
        printed_rows, rca_info, batch_name, test_selector, github_compare = build_case_rows(
            sorted_cases, case_duration_lookup, build_id
        )

        html.append("<div class='subcard unique'>")

        html.append(f"<h3>Error Type</h3><pre>{escape(error)}</pre>")

        if subtask_case_pairs:
            first_subtask_id = subtask_case_pairs[0][0]
            subtask_url = f"https://testray.liferay.com/web/testray#/testflow/{task_id}/subtasks/{first_subtask_id}"
            html.append(f"<p><a href='{subtask_url}' target='_blank'>Testray Subtask</a></p>")

        if is_module_integration_test(subtask_case_pairs[0][1]):
            result = case_id_to_result.get(subtask_case_pairs[0][1])
            error_link = get_error_messages_link(result) if result else None
            if error_link:
                html.append(f"<p><a href='{error_link}' target='_blank'>Failure Messages</a></p>")

        html.append("<table><tr><th>Case Name</th><th>Duration</th><th>Component</th></tr>")
        for row in printed_rows:
            name, duration, component = map(escape, row)
            html.append(f"<tr><td>{name}</td><td>{duration}</td><td>{component}</td></tr>")
        html.append("</table>")

        if batch_name and test_selector and github_compare:
            html.append(build_rca_html_block(batch_name, test_selector, github_compare))

        html.append("</div>")  # End subcard

    html.append("</div>")  # End Unique Errors Main Card

    # Final closing tags
    html.append("</div>")  # End grid
    html.append("</body></html>")


    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html))

    # Automatically open in default browser
    absolute_path = os.path.abspath(output_path)
    webbrowser.open(f"file://{absolute_path}")