#!/usr/bin/env python3

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..')))

from utils.liferay_utils.testray_utils.testray_helpers import *
from utils.liferay_utils.jira_utils.jira_liferay import get_jira_connection
from utils.liferay_utils.jira_utils.jira_helpers import get_all_issues, __initialize_task, get_team_components
from utils.liferay_utils.testray_utils.testray_api import *

def find_lpd_tasks_for_tests(jira_connection, test_names):
    """
    Look for open LPD Jira tasks whose titles contain any of the given test names.

    Args:
        jira_connection: Authenticated Jira connection
        test_names: List of test names to search for

    Returns:
        Dict mapping test name -> LPD issue key or 'NO TASK CREATED'
    """
    lpd_mapping = {}

    for test_name in test_names:
        try:
            # JQL to find open issues in LPD project whose summary contains the test name
            jql = f'project = LPD AND summary ~ "{test_name}" AND status != Closed'
            issues = jira_connection.search_issues(jql)

            if issues:
                # Take first matching issue key (can extend to multiple if needed)
                lpd_mapping[test_name] = issues[0].key
            else:
                lpd_mapping[test_name] = "NO TASK CREATED"

        except Exception as e:
            print(f"Error searching for test '{test_name}': {e}")
            lpd_mapping[test_name] = "NO TASK CREATED"

    return lpd_mapping


# ------------------------ ENTRY POINT ------------------------
if __name__ == "__main__":
    test_names = [
        "Staging#CanEnableAdvancedStagingConfigurationInSystemSetting",
        "Staging#StagingOnlyApprovedPublishToLive",
        "RemoteStaging#PublishMoreWebContentsToRemoteWithMultiPublishOption",
        "CPSitetemplates#AssertNoChildPageOptionForPageDerivedFromSiteTemplate",
        "StagingUsecase#AssertAssetPriorityNotBeResetAfterPublication",
        "Staging#CanEnableAdvancedStagingConfigurationInInstanceSetting",
        "SitetemplatesUsecase#SitesTemplateResourceInheritance",
        "CreateAPIDefinitionObjectsAsBackendOfAPIBuilder#CanCreateUnpublishedAPIApplication",
        "Staging#ConfigureIgnorePreviewsAndThumbnails",
        "Staging#StagingRemoteWithDynamicExportLimit",
        "Staging#PublishMoreWebContentWithMultiPublishOption",
        "PGStaging#PublishLayoutIconDeletion",
        "Staging#CanPublishWCWithURLReferenceViaRemoteStaging",
        "StagingUsecaseWithVersioning#StagingPageLogo",
        "StagingUsecaseWithVersioning#ViewPublishedContentAfterStagingUndo",
        "PGStaging#PublishWCWithFriendlyURL",
        "com.liferay.object.rest.internal.resource.v1_0.test.ObjectEntryResourceTest",
        "com.liferay.object.rest.internal.manager.v1_0.test.DefaultObjectEntryManagerImplTest",
        "ExportImport#AssetLinksCanBeConfiguredWhenExport",
        "SitetemplatesStaging#CanEnableLocalStagingOnTeamExtranetSiteTemplate",
        "com.liferay.staging.test.StagingDataPortletPreferencesTest",
        "ImportExport#CanDownloadSampleImportFile",
        "Staging#ActivateStagingWithWCDOnFragment",
        "ExportImport#AssertStagingSystemSettingsInfo",
        "FilterParentCustomEntitiesWithDataFromRelatedElementsPt1#CanFilterParentObjectEntriesInManyToManyRelByChildObjectLongTextAfterPublishingObje",
        "SitesExportImport#ExportImportSiteWithEmbeddedWCDInFragment",
        "FilterCustomObjectsMultipleLevelsRelationshipsWithComparisonOperators#CanFilterMultipleLevelsWithComparisonEqByCustomField",
        "FilterCustomObjectsMultipleLevelsRelationshipsWithComparisonOperators#CanFilterMultipleLevelsWithComparisonGe",
        "ImportAndExportObjects#DownloadSampleFileOfTheCustomObject"
    ]

    jira_conn = get_jira_connection()
    lpd_results = find_lpd_tasks_for_tests(jira_conn, test_names)
    jira_conn.close()

    # Print results
    for test, task in lpd_results.items():
        print(f"{test} -> {task}")
