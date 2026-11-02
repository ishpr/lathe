# Exporting to trackers

The markdown document is the source of truth; the tracker holds the working copy. Field names
and hierarchy rules differ by product, edition and instance configuration, so run a two-row test
import before the real one, and say in the chat summary which fields you mapped. When unsure,
produce the CSV and a two-line note on how to import it rather than guessing at an API.

## Mapping

| Document element | Jira (Software) | Linear | Azure DevOps (Agile process) | GitHub |
|---|---|---|---|---|
| Epic | Epic (issue type) | Project (under an Initiative if used) | Epic work item | Milestone, or a tracking issue with sub-issues |
| Feature | A custom Feature type between Epic and Story (Plans / Advanced Roadmaps hierarchy), or a Story with the Epic as parent plus a feature label | Issue in the Project, with the stories as sub-issues; or a Project milestone | Feature work item (child of the Epic) | Issue (type Feature if the org has issue types) with sub-issues |
| Story | Story, child of the Epic (in standard Jira only an Epic can parent a Story; below a Story sit sub-tasks) | Issue in the Project (nest as a sub-issue in the UI or API) | User Story (child of the Feature); the Scrum process calls it a Product Backlog Item | Issue (sub-issue of the feature issue) |
| Acceptance criteria | Description, or the "Acceptance Criteria" custom field if the project has one | Description | "Acceptance Criteria" field (Agile template has one) | Issue body, as a task list reviewers can tick |
| Size | Story Points | Estimate | Story Points (Scrum: Effort) | Label or a Projects field |
| Non-goals, decisions, assumptions | Epic description | Project description | Epic description | Milestone description / tracking issue |
| Metric | Epic description (first lines) | Project description or a Project update | Epic description | Milestone description |

## Jira CSV (bulk import)

```csv
Work item ID,Issue Type,Summary,Description,Parent,Labels,Story Points
1,Epic,Customers answer "where is my order" themselves,"<problem + hypothesis + metrics + non-goals>",,self-service,
2,Story,Open my order's status from the confirmation email,"As a ... so that ...

Acceptance criteria
- Given ... when ... then ...",1,feature-see-status,3
```

Notes: Jira Cloud — add a Work item ID column and map Parent to it; list parents before children.
Jira Data Center/Server (and some company-managed projects) — link stories with Epic Link = the
epic's Epic Name, and fill Epic Name on epic rows; Parent ID is for sub-tasks and Parent Link for
Advanced Roadmaps. Multi-line descriptions must be quoted. Check which your instance uses with a
two-row test import. Sources:
https://support.atlassian.com/jira-cloud-administration/docs/import-data-from-a-csv-file/ and
https://support.atlassian.com/jira/kb/how-to-import-issues-with-hierarchy-from-a-csv-into-jira-software/

## Linear

Linear's hierarchy is Initiative → Project → Issue → Sub-issue. Linear's CSV import (Settings →
Administration → Import/Export) creates flat issues (Title, Description, Priority, Status, Labels,
Estimate; use the columns from Linear's exported template). It does not create Projects or
sub-issues, so create the Project by hand (or via the API/CLI), paste the epic text in its
description, import the stories as issues, then assign them to the Project and nest sub-issues in
the UI or via the API. Put the metric in a Project update so it is visible on the project page.
Source: https://linear.app/docs/cli-importer

## Azure DevOps

The Agile process has the exact ladder: Epic → Feature → User Story (→ Task). CSV import
(Boards → Queries → Import work items) needs Work Item Type and Title. It links parent and child
only by indenting the title: the Epic row has its name in Title 1, its Features have theirs in
Title 2, and Stories in Title 3. There is no Parent column. Do not include ID for new items, nor
State. Maximum 1,000 items per import. Header example:

```csv
Work Item Type,Title 1,Title 2,Title 3,Description,Acceptance Criteria,Story Points
```

The Scrum process calls the story a Product Backlog Item and uses Effort instead of Story Points.
Source: https://learn.microsoft.com/en-us/azure/devops/boards/queries/import-work-items-from-csv

## GitHub

Use sub-issues (generally available since April 2025; up to 100 per parent, 8 levels deep) for
epic → feature → story. If the organization has issue types enabled, set the type on each level;
otherwise use a label. Keep a task list only for repositories or GitHub Enterprise Server versions
without sub-issues. One issue per story with the Given/When/Then criteria as a task list so
reviewers can tick them; a milestone per epic or a tracking issue whose body is the epic document;
GitHub Projects fields for size and the metric. Sources:
https://github.blog/changelog/2025-04-09-evolving-github-issues-and-projects/ and
https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues
