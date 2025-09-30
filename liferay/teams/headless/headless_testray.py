#!/usr/bin/env python3

import sys
import os
from collections import defaultdict
from traceback import print_tb

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..')))

from utils.liferay_utils.testray_utils.testray_helpers import *
from utils.liferay_utils.jira_utils.jira_liferay import get_jira_connection
from utils.liferay_utils.jira_utils.jira_helpers import get_all_issues
from utils.liferay_utils.testray_utils.testray_api import *

# ------------------------ MAIN FUNCTION ------------------------
def analyze_testflow(jira_connection):
    builds = get_routine_to_builds()
    latest_build = get_latest_done_build(builds)

    if not latest_build:
        return
    latest_build_id = latest_build["id"]

    task_id = find_or_create_task(latest_build,jira_connection, latest_build_id)
    if not task_id:
        print("✘ Could not find or create a valid task, exiting.")
        return

    status = get_task_status(task_id)
    status_key = status.get("dueStatus", {}).get("key")
    if status_key == "COMPLETE":
        print(f"✔ Task {task_id} for build {latest_build_id} is now complete. No further processing required.")
        return

    print(f"✔ Using build {latest_build_id} and task {task_id}")

    quarter_start, quarter_number, year = get_current_quarter_info()

    jql = (
        f"text ~ '{year} Milestone {quarter_number} \\\\| Testing activities \\\\[Headless\\\\]' "
        f"and type = Epic and project='PUBLIC - Liferay Product Delivery' and status != Closed"
    )

    related_epics = get_all_issues(jira_connection, jql, fields=["summary", "key"])
    print(f"✔ Retrieved {len(related_epics)} related Epics from JIRA")

    epic = related_epics[0] if len(related_epics) == 1 else None

    if epic:
        print(f"✔ Found testing epic: {epic}")
    else:
        print(f"✘ Expected 1 related epic, but found {len(related_epics)}")

    latest_complete = get_latest_build_with_completed_task(builds)
    if latest_complete:
        print("Autofill from latest analysed build...")
        autofill_build(latest_complete["id"], latest_build["id"])
        print(f"✔ Completed")

    subtasks = get_task_subtasks(task_id)

    total_unique = 0
    unique_tasks = []
    case_ids = set()
    batch_updates = []
    case_id_to_result = {}
    subtasks_to_complete = []
    subtask_to_issues = defaultdict(set)
    case_history_cache = {}

    for subtask in subtasks:
        subtask_id = subtask["id"]
        summary_results = get_subtask_case_results(subtask_id)
        if not summary_results:
            continue
        if subtask.get("dueStatus", {}).get("key") == "COMPLETE":
            issues_field = subtask.get("issues")
            if issues_field:
                continue
            issues_in_subtask = set()
            for summary_result in summary_results:
                issues = summary_result.get("issues")
                if issues:
                    issues_in_subtask.add(issues)

            if not issues_in_subtask:
                continue

            issues_to_add = (
                issues_in_subtask.pop() if len(issues_in_subtask) == 1
                else ", ".join(issues_in_subtask)
            )

            update_subtask_status(subtask_id, issues=issues_to_add)
            continue

        all_results_in_subtask_handled = True
        first_result = True
        subtask_unique_failures = []

        for summary_result in summary_results:
            result_handled, flaky_info, is_blocked_unique = process_summary_result(
                summary_result=summary_result,
                task_id=task_id,
                subtask_id=subtask_id,
                first_result=first_result,
                latest_build_id=latest_build_id,
                jira_conn=jira_connection,
                epic=epic,
                batch_updates=batch_updates,
                unique_tasks=unique_tasks,
                case_ids=case_ids,
                case_id_to_result=case_id_to_result,
                history_cache=case_history_cache,
            )
            first_result = False

            if not result_handled:
                all_results_in_subtask_handled = False

            if is_blocked_unique:
                subtask_unique_failures.append({
                    "error": summary_result["errors"],
                    "subtask_id": subtask_id,
                    "case_id": summary_result["r_caseToCaseResult_c_caseId"],
                    "component_id": summary_result["r_componentToCaseResult_c_componentId"],
                    "result_id": summary_result["id"]
                })

        if subtask_unique_failures:
            # Unpack tuple since return_list=False by default
            has_similar_issue, blocked_dict = find_similar_open_issues(
                jira_connection,
                subtask_unique_failures[0]["case_id"],
                subtask_unique_failures[0]["error"]
            )

            if has_similar_issue and blocked_dict:
                # Use the issues string directly from blocked_dict
                issue_keys_str = blocked_dict["issues"]
                subtask_to_issues[subtask_id].add(issue_keys_str)
                all_results_in_subtask_handled = True

                for failure in subtask_unique_failures:
                    batch_updates.append({
                        "id": failure["result_id"],
                        "dueStatus": blocked_dict["dueStatus"],
                        "issues": issue_keys_str
                    })
            else:
                # No similar issue found → create new investigation task subtask_id = subtask_unique_failures[0]["case_id"]
                print(f"No similar issue found → create new investigation task for subtask {subtask_id}")
                issue = create_investigation_task_for_subtask(
                    subtask_unique_failures=subtask_unique_failures,
                    subtask_id=subtask_id,
                    latest_build_id=latest_build_id,
                    jira_connection=jira_connection,
                    epic=epic,
                    task_id=task_id,
                    case_history_cache=case_history_cache,
                )

                if issue:
                    issue_key = issue.key
                    subtask_to_issues[subtask_id].add(issue_key)
                    all_results_in_subtask_handled = True

                    for failure in subtask_unique_failures:
                        batch_updates.append({
                            "id": failure["result_id"],
                            "dueStatus": {"key": "BLOCKED", "name": "Blocked"},
                            "issues": issue_key
                        })


        if all_results_in_subtask_handled:
                    subtasks_to_complete.append(subtask_id)

    if batch_updates:
        assign_issue_to_case_result_batch(batch_updates)

    for subtask_id in subtasks_to_complete:
        issues = list(subtask_to_issues[subtask_id])

        if not issues:
            issues_to_add = None
        elif len(issues) == 1:
            issues_to_add = issues[0]
        else:
            issues_to_add = ", ".join(issues)

        print(f"✔ Marking subtask {subtask_id} as complete and associating issues: {issues_to_add}")
        update_subtask_status(subtask_id, issues=issues_to_add)

    subtasks = get_task_subtasks(task_id)
    task_completed = check_and_complete_task_if_all_subtasks_done(
        task_id, subtasks, jira_connection, latest_build_id
    )

    if not task_completed:
        print(f"✔ Task {task_id} is not completed. Further processing required.")
    else:
        print(f"✔ Task {task_id} is now complete. No further processing required.")

def get_automated_functional_tests_ratio():
    builds = get_routine_to_builds()
    latest_build = get_latest_done_build(builds)
    if not latest_build:
        return

    beginning_of_current_quarter_build_id = get_build_from_beginning_of_current_quarter(builds)
    latest_build_id = latest_build['id']

    start_of_quarter_count = count_automated_functional_cases(get_all_cases_info_from_build(beginning_of_current_quarter_build_id))
    current_count = count_automated_functional_cases(get_all_cases_info_from_build(latest_build_id))

    report_poshi_tests_decrease(start_of_quarter_count, current_count)

# ------------------------ ENTRY POINT ------------------------
if __name__ == "__main__":
    jira_conn = get_jira_connection()
    analyze_testflow(jira_conn)
    jira_conn.close()
    #get_automated_functional_tests_ratio()