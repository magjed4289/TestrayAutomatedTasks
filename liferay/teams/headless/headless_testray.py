#!/usr/bin/env python3

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..')))

from utils.liferay_utils.testray_utils.testray_helpers import *
from utils.liferay_utils.jira_utils.jira_liferay import get_jira_connection
from utils.liferay_utils.jira_utils.jira_helpers import get_all_issues, __initialize_task, get_team_components
from utils.liferay_utils.testray_utils.testray_api import *

# ------------------------ MAIN FUNCTION ------------------------
def analyze_testflow(jira_connection):
    builds = get_routine_to_builds()
    latest_build = get_latest_done_build(builds)

    if not latest_build:
        return
    latest_build_id = latest_build["id"]

    task_id = find_or_create_task(latest_build)
    if not task_id:
        print("✘ Could not find or create a valid task, exiting.")
        return

    status = get_task_status(task_id)
    status_key = status.get("dueStatus", {}).get("key")
    if  status_key == "COMPLETE":
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
        autofill_build(latest_complete["id"], latest_build_id)
        print(f"✔ Completed")

    subtasks = get_task_subtasks(task_id)

    total_unique = 0
    total_flaky_without_issue = []
    unique_tasks = []
    case_ids = set()
    batch_updates = []
    case_id_to_result = {}

    subtask_results = {}

    for subtask in subtasks:
        subtask_id = subtask["id"]
        if subtask.get("dueStatus", {}).get("key") == "COMPLETE":
            continue

        summary_results = get_subtask_case_results(subtask_id)
        subtask_results[subtask_id] = summary_results

        first_result = True
        for summary_result in summary_results:
            result_handled, flaky_info, unique_flag = process_summary_result(
                summary_result=summary_result,
                subtask_id=subtask_id,
                summary_results=summary_results,
                first_result=first_result,
                latest_build_id=latest_build_id,
                jira_conn=jira_connection,
                epic=epic,
                batch_updates=batch_updates,
                unique_tasks=unique_tasks,
                case_ids=case_ids,
                case_id_to_result=case_id_to_result,
                total_flaky_without_issue=total_flaky_without_issue,
            )

            first_result = False

            if unique_flag:
                total_unique += 1

    if batch_updates:
        assign_issue_to_case_result_batch(batch_updates)

    for subtask in subtasks:
        subtask_id = subtask["id"]
        summary_results = subtask_results.get(subtask_id, [])

        if all(result.get("issues") for result in summary_results):
            if subtask.get("dueStatus", {}).get("key") != "COMPLETE":
                print(f"✔ Marking subtask {subtask_id} as complete")
                update_subtask_status(subtask_id)

    task_completed = check_and_complete_task_if_all_subtasks_done(task_id)

    if not task_completed:
        generate_combined_html_report(
            flaky_tests=total_flaky_without_issue,
            unique_tasks=unique_tasks,
            task_id=task_id,
            case_id_to_result=case_id_to_result,
            build_id=latest_build_id,
            output_path="reports/combined_report.html",
        )
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

    report_poshi_tests_decrease(start_of_quarter_count,current_count)

# ------------------------ ENTRY POINT ------------------------
if __name__ == "__main__":
    jira_conn = get_jira_connection()
    analyze_testflow(jira_conn)
    jira_conn.close()
    get_automated_functional_tests_ratio()