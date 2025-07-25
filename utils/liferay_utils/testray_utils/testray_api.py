#!/usr/bin/env python3

import base64
import requests
from dotenv import load_dotenv

import os
from pathlib import Path

# ------------------------ AUTH CONFIG ------------------------

env_path = Path(__file__).resolve().parents[3].parent / ".automated_tasks.env"

load_dotenv(dotenv_path=env_path)

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

TOKEN_URL = "https://testray.liferay.com/o/oauth2/token"

SESSION_ID = os.getenv("SESSION_ID")
CSRF_TOKEN = os.getenv("CSRF_TOKEN")

# =============================== CONSTANTS ===============================

BASE_URL = "https://testray.liferay.com/o/c"
TESTRAY_REST_URL = "https://testray.liferay.com/o/testray-rest/v1.0"
HEADLESS_ROUTINE_ID = 994140
EE_PULL_REQUEST_ROUTINE_ID = 45357


def get_access_token():
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data={"grant_type": "client_credentials"},
    )
    response.raise_for_status()
    return response.json()["access_token"]


ACCESS_TOKEN = get_access_token()

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json"
}

HEADERS2 = {
    "Cookie": f"JSESSIONID={SESSION_ID}",
    "x-csrf-token": {CSRF_TOKEN},
    "Accept": "application/json"
}
# Status filters
STATUS_FAILED_BLOCKED_TESTFIX = "FAILED,TESTFIX,BLOCKED"
STATUS_FAILED_PASSED = "FAILED,PASSED"


# ============================ HTTP HELPERS ============================

def get_json(url):
    """Send GET request and return JSON response."""
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()

def put_json(url, payload):
    """Send PUT request with JSON payload."""
    headers = HEADERS.copy()
    headers["Content-Type"] = "application/json"
    response = requests.put(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()


# ============================ API OPERATIONS ============================

def assign_issue_to_case_result_batch(batch_updates):
    """Update a batch of case results with issues and due statuses."""
    for item in batch_updates:
        case_result_id = item["id"]
        payload = {
            "dueStatus": item["dueStatus"],
            "issues": item["issues"]
        }
        url = f"{BASE_URL}/caseresults/{case_result_id}"
        put_json(url, payload)


def autofill_build(testray_build_id_1, testray_build_id_2):
    """Trigger autofill between two Testray builds."""
    url = f"{TESTRAY_REST_URL}/testray-build-autofill/{testray_build_id_1}/{testray_build_id_2}"
    response = requests.post(url, headers=HEADERS, data="")
    response.raise_for_status()
    return response.json()


def complete_task(task_id):
    url = f"{BASE_URL}/tasks/{task_id}"
    payload = {
        "dueStatus": {
            "key": "COMPLETE",
            "name": "Complete"
        }
    }
    headers = HEADERS.copy()
    headers["Content-Type"] = "application/json"
    response = requests.patch(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()


def create_task(build):
    """Create a task for a build."""
    payload = {
        "name": build["name"],
        "r_buildToTasks_c_buildId": build["id"],
        "dueStatus": {
            "key": "INANALYSIS",
            "name": "In Analysis"
        }
    }
    url = f"{BASE_URL}/tasks/"
    headers = HEADERS.copy()
    headers["Content-Type"] = "application/json"
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()


def create_testflow(task_id):
    """Create testflow for a task."""
    url = f"{TESTRAY_REST_URL}/testray-testflow/{task_id}"
    response = requests.post(url, headers=HEADERS, data="")
    response.raise_for_status()
    return response.json()


def fetch_case_results(case_id, routine_id, status=None, page_size=500):
    base_url = f"{TESTRAY_REST_URL}/testray-case-result-history/{case_id}"
    page = 1
    all_items = []

    while True:
        params = (
                f"testrayRoutineIds={routine_id}"
                + (f"&status={status}" if status else "")
                + f"&page={page}&pageSize={page_size}"
        )
        url = f"{base_url}?{params}"
        result = get_json(url)
        items = result.get("items", [])
        all_items.extend(items)

        if len(items) < page_size:
            break  # Last page reached
        page += 1

    return all_items

def get_all_cases_info_from_build(build_id):
    """Fetch all case results for a given build (paginated)."""
    page = 1
    all_cases_info = []

    while True:
        url = f"{BASE_URL}/builds/{build_id}/buildToCaseResult?fields=r_caseToCaseResult_c_case&nestedFields=r_caseToCaseResult_c_case&pageSize=500&page={page}"
        data = get_json(url)
        items = data.get("items", [])
        all_cases_info.extend(items)

        if len(items) < 500:
            break
        page += 1

    return all_cases_info

def get_all_build_case_results(build_id):
    """Fetch all case results for a given build (paginated)."""
    page = 1
    all_items = []

    while True:
        url = f"{BASE_URL}/builds/{build_id}/buildToCaseResult?pageSize=500&page={page}"
        data = get_json(url)
        items = data.get("items", [])
        all_items.extend(items)

        if len(items) < 500:
            break
        page += 1

    return all_items

def get_build_info(build_id):
    """Get build metadata, including routine ID and due date."""
    url = f"{BASE_URL}/builds/{build_id}?fields=dueDate,gitHash,name,id,importStatus,r_routineToBuilds_c_routineId&nestedFields=buildToTasks"
    return get_json(url)


def get_build_tasks(build_id):
    """Get tasks associated with a build."""
    url = f"{BASE_URL}/builds/{build_id}/buildToTasks?fields=id,dueStatus"
    return get_json(url).get("items", [])


def get_case_info(case_id):
    """Get the name and priority of a test case."""
    url = f"{BASE_URL}/cases/{case_id}"
    return get_json(url)


def get_case_result(case_result_id):
    url = f"{BASE_URL}/caseresults/{case_result_id}"
    return get_json(url)


def get_case_type_name(case_type_id):
    """Get name of a case type by ID."""
    url = f"{BASE_URL}/casetypes/{case_type_id}?fields=name"
    return get_json(url).get("name", "Unknown")


def get_component_name(component_id):
    """Get name of a component by ID."""
    url = f"{BASE_URL}/components/{component_id}?fields=name"
    return get_json(url).get("name", "Unknown")


def get_routine_builds(routine_id, page=1):
    """List builds for a routine by routine ID."""
    url = f"{BASE_URL}/builds?filter=r_routineToBuilds_c_routineId eq '{routine_id}'&pageSize=100&page={page}&sort=dateCreated:desc"
    return get_json(url)


def get_routine_to_builds():
    """Fetch all builds for a routine, remove pagination and sort by dateCreated descending."""
    url = f"{BASE_URL}/routines/{HEADLESS_ROUTINE_ID}/routineToBuilds?fields=dueDate,name,id,importStatus,r_routineToBuilds_c_routineId,dateCreated&pageSize=-1"
    items = get_json(url).get("items", [])
    # Sort by dateCreated descending; fallback to empty string if missing
    return sorted(items, key=lambda b: b.get("dateCreated", ""), reverse=True)


def get_subtask_case_results(subtask_id):
    """Get case results under a subtask."""
    url = f"{BASE_URL}/subtasks/{subtask_id}/subtaskToCaseResults?fields=id,executionDate,errors,issues,r_caseToCaseResult_c_caseId"
    return get_json(url).get("items", [])


def get_task_build_id(task_id):
    """Get build ID associated with a task."""
    url = f"{BASE_URL}/tasks/{task_id}?fields=r_buildToTasks_c_buildId"
    return get_json(url).get("r_buildToTasks_c_buildId")

def get_task_status(task_id):
    """Get the status of a task."""
    url = f"{BASE_URL}/tasks/{task_id}?fields=dueStatus"
    return get_json(url)

def get_task_subtasks(task_id):
    """Get subtasks associated with a task."""
    url = f"{BASE_URL}/tasks/{task_id}/taskToSubtasks?pageSize=-1"
    return get_json(url).get("items", [])


def reanalyze_task(task_id):
    """Set an abandoned task's dueStatus back to INANALYSIS."""
    url = f"{BASE_URL}/tasks/{task_id}"
    payload = {
        "dueStatus": {
            "key": "INANALYSIS",
            "name": "In Analysis"
        }
    }
    headers = HEADERS.copy()
    headers["Content-Type"] = "application/json"
    response = requests.patch(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()


def update_subtask_status(subtask_id):
    """Mark a subtask as complete."""
    url = f"{BASE_URL}/subtasks/{subtask_id}"
    payload = {
        "dueStatus": {
            "key": "COMPLETE",
            "name": "Complete"
        }
    }
    put_json(url, payload)
    print(f"Subtask {subtask_id} marked as COMPLETE.")