# API coverage

Every Affinity API operation and how the SDK supports it. Generated from
`tools/api_coverage.json` by `python tools/api_coverage.py --markdown`; CI checks it
against the pinned API specs and checks that each supported call requests its operation.

## V2 API

82 supported, 30 planned

| Operation | Status | SDK | Notes |
|---|---|---|---|
| `GET /v2/auth/whoami` | supported | `auth.whoami()` |  |
| `GET /v2/calls` | supported | `interactions.list_calls()` |  |
| `GET /v2/chat-messages` | supported | `interactions.list_chat_messages()` |  |
| `GET /v2/companies` | supported | `companies.list()` |  |
| `GET /v2/companies/fields` | supported | `companies.get_fields()` |  |
| `GET /v2/companies/fields/{fieldId}/dropdown-options` | supported | `companies.get_field_dropdown_options(FieldId(1))` |  |
| `POST /v2/companies/search` | planned |  | beta; phase 2.x-3 |
| `GET /v2/companies/{companyId}` | supported | `companies.get(CompanyId(1))` |  |
| `GET /v2/companies/{companyId}/fields` | supported | `companies.get_field_values(CompanyId(1))` | beta |
| `PATCH /v2/companies/{companyId}/fields` | supported | `companies.batch_update_fields(CompanyId(1), {"field-1": "x"})` |  |
| `GET /v2/companies/{companyId}/fields/{fieldId}` | planned |  | beta; phase 2.x-4 |
| `POST /v2/companies/{companyId}/fields/{fieldId}` | planned |  | phase 2.x-4 |
| `GET /v2/companies/{companyId}/fields/{fieldId}/values` | planned |  | beta; API 2026-09-17+; phase 2.x-4 |
| `GET /v2/companies/{companyId}/list-entries` | supported | `companies.get_list_entries(CompanyId(1))` |  |
| `GET /v2/companies/{companyId}/lists` | supported | `companies.get_lists(CompanyId(1))` |  |
| `GET /v2/companies/{companyId}/notes` | supported | `companies.list_notes(CompanyId(1))` |  |
| `GET /v2/companies/{companyId}/relationships` | supported | `companies.list_relationships(CompanyId(1))` |  |
| `GET /v2/company-merges` | supported | `companies.list_merges()` |  |
| `POST /v2/company-merges` | supported | `companies.merge(CompanyId(1), CompanyId(2))` |  |
| `GET /v2/company-merges/{mergeId}` | supported | `companies.get_merge_state(1)` |  |
| `GET /v2/duplicates/company-suggestions` | planned |  | beta; API 2026-07-15+; phase 2.x-2 |
| `GET /v2/duplicates/person-suggestions` | planned |  | beta; API 2026-07-15+; phase 2.x-2 |
| `GET /v2/emails` | supported | `interactions.list_emails()` |  |
| `POST /v2/feedback` | planned |  | phase 2.x-1 |
| `GET /v2/field-value-changes` | supported | `field_value_changes.list_global(field_id=FieldId(1))` |  |
| `GET /v2/files` | supported | `files.feed()` | beta; API 2026-09-17+ |
| `POST /v2/files/search` | supported | `files.search("prompt text")` |  |
| `GET /v2/files/{fileId}` | supported | `files.get_download_url(FileId(1))` | beta; API 2026-09-17+ |
| `GET /v2/inferred-connections/coworkers` | planned |  | beta; phase 2.x-2 |
| `GET /v2/inferred-connections/investor-executive-connections` | planned |  | beta; phase 2.x-2 |
| `GET /v2/lists` | supported | `lists.list()` |  |
| `POST /v2/lists` | supported | `lists.create(ListCreate(name="x", type=ListType.PERSON, is_public=False))` |  |
| `GET /v2/lists/{listId}` | supported | `lists.get(ListId(1))` |  |
| `GET /v2/lists/{listId}/fields` | supported | `lists.get_fields(ListId(1))` |  |
| `GET /v2/lists/{listId}/fields/{fieldId}/dropdown-options` | supported | `lists.get_field_dropdown_options(ListId(1), FieldId(1))` |  |
| `POST /v2/lists/{listId}/fields/{fieldId}/dropdown-options` | supported | `lists.create_field_dropdown_option(ListId(1), FieldId(1), option_type="dropdown", text="x")` |  |
| `DELETE /v2/lists/{listId}/fields/{fieldId}/dropdown-options/{dropdownOptionId}` | supported | `lists.delete_field_dropdown_option(ListId(1), FieldId(1), 1)` |  |
| `GET /v2/lists/{listId}/fields/{fieldId}/dropdown-options/{dropdownOptionId}` | supported | `lists.get_field_dropdown_option(ListId(1), FieldId(1), 1)` |  |
| `POST /v2/lists/{listId}/fields/{fieldId}/dropdown-options/{dropdownOptionId}` | supported | `lists.update_field_dropdown_option(ListId(1), FieldId(1), 1, text="x")` |  |
| `GET /v2/lists/{listId}/list-entries` | supported | `lists.entries(ListId(1)).list()` |  |
| `POST /v2/lists/{listId}/list-entries` | planned |  | beta; API 2026-09-17+; phase when-live |
| `POST /v2/lists/{listId}/list-entries/search` | planned |  | beta; phase 2.x-3 |
| `GET /v2/lists/{listId}/list-entries/{listEntryId}` | supported | `lists.entries(ListId(1)).get(ListEntryId(1))` |  |
| `GET /v2/lists/{listId}/list-entries/{listEntryId}/field-value-changes` | planned |  | phase 2.x-4 |
| `GET /v2/lists/{listId}/list-entries/{listEntryId}/fields` | supported | `lists.entries(ListId(1)).get_field_values(ListEntryId(1))` |  |
| `PATCH /v2/lists/{listId}/list-entries/{listEntryId}/fields` | supported | `lists.entries(ListId(1)).batch_update_fields(ListEntryId(1), {"field-1": "x"})` |  |
| `GET /v2/lists/{listId}/list-entries/{listEntryId}/fields/{fieldId}` | supported | `lists.entries(ListId(1)).get_field_value(ListEntryId(1), FieldId(1))` |  |
| `POST /v2/lists/{listId}/list-entries/{listEntryId}/fields/{fieldId}` | supported | `lists.entries(ListId(1)).update_field_value(ListEntryId(1), FieldId(1), "x")` |  |
| `GET /v2/lists/{listId}/list-entries/{listEntryId}/fields/{fieldId}/values` | planned |  | beta; API 2026-09-17+; phase 2.x-4 |
| `GET /v2/lists/{listId}/saved-views` | supported | `lists.get_saved_views(ListId(1))` |  |
| `GET /v2/lists/{listId}/saved-views/{viewId}` | supported | `lists.get_saved_view(ListId(1), SavedViewId(1))` |  |
| `GET /v2/lists/{listId}/saved-views/{viewId}/list-entries` | supported | `lists.entries(ListId(1)).from_saved_view(SavedViewId(1))` |  |
| `GET /v2/meetings` | supported | `interactions.list_meetings()` |  |
| `GET /v2/meetings/{meetingId}` | planned |  | beta; API 2026-07-15+; phase 2.x-1 |
| `GET /v2/notes` | supported | `notes.feed()` |  |
| `POST /v2/notes` | supported | `notes.create(NoteCreate(content="x", person_ids=[PersonId(1)]))` |  |
| `POST /v2/notes/search` | supported | `notes.search("prompt text")` |  |
| `DELETE /v2/notes/{noteId}` | supported | `notes.delete(NoteId(1))` |  |
| `GET /v2/notes/{noteId}` | supported | `notes.get(NoteId(1))` |  |
| `POST /v2/notes/{noteId}` | supported | `notes.update(NoteId(1), NoteUpdate(content="x"))` |  |
| `GET /v2/notes/{noteId}/attached-companies` | planned |  | phase 2.x-1 |
| `GET /v2/notes/{noteId}/attached-opportunities` | planned |  | phase 2.x-1 |
| `GET /v2/notes/{noteId}/attached-persons` | planned |  | phase 2.x-1 |
| `GET /v2/notes/{noteId}/replies` | supported | `notes.list_replies(NoteId(1))` |  |
| `GET /v2/opportunities` | supported | `opportunities.list()` |  |
| `DELETE /v2/opportunities/{opportunityId}` | planned |  | beta; API 2026-09-17+; phase when-live |
| `GET /v2/opportunities/{opportunityId}` | supported | `opportunities.get(OpportunityId(1))` |  |
| `POST /v2/opportunities/{opportunityId}` | planned |  | beta; API 2026-09-17+; phase when-live |
| `GET /v2/opportunities/{opportunityId}/notes` | supported | `opportunities.list_notes(OpportunityId(1))` |  |
| `GET /v2/person-merges` | supported | `persons.list_merges()` |  |
| `POST /v2/person-merges` | supported | `persons.merge(PersonId(1), PersonId(2))` |  |
| `GET /v2/person-merges/{mergeId}` | supported | `persons.get_merge_state(1)` |  |
| `GET /v2/persons` | supported | `persons.list()` |  |
| `POST /v2/persons` | planned |  | beta; API 2026-07-15+; phase when-live |
| `GET /v2/persons/fields` | supported | `persons.get_fields()` |  |
| `GET /v2/persons/fields/{fieldId}/dropdown-options` | supported | `persons.get_field_dropdown_options(FieldId(1))` |  |
| `POST /v2/persons/search` | planned |  | beta; phase 2.x-3 |
| `DELETE /v2/persons/{personId}` | planned |  | beta; API 2026-07-15+; phase when-live |
| `GET /v2/persons/{personId}` | supported | `persons.get(PersonId(1))` |  |
| `GET /v2/persons/{personId}/fields` | supported | `persons.get_field_values(PersonId(1))` | beta |
| `PATCH /v2/persons/{personId}/fields` | supported | `persons.batch_update_fields(PersonId(1), {"field-1": "x"})` |  |
| `GET /v2/persons/{personId}/fields/{fieldId}` | planned |  | beta; phase 2.x-4 |
| `POST /v2/persons/{personId}/fields/{fieldId}` | planned |  | phase 2.x-4 |
| `GET /v2/persons/{personId}/fields/{fieldId}/values` | planned |  | beta; API 2026-09-17+; phase 2.x-4 |
| `GET /v2/persons/{personId}/list-entries` | supported | `persons.get_list_entries(PersonId(1))` |  |
| `GET /v2/persons/{personId}/lists` | supported | `persons.get_lists(PersonId(1))` |  |
| `GET /v2/persons/{personId}/notes` | supported | `persons.list_notes(PersonId(1))` |  |
| `GET /v2/persons/{personId}/relationships` | supported | `persons.list_relationships(PersonId(1))` |  |
| `GET /v2/rate-limit` | supported | `rate_limits.refresh()` | beta; API 2026-07-15+ |
| `GET /v2/reminders` | supported | `reminders.list()` |  |
| `POST /v2/reminders` | supported | `reminders.create(ReminderCreate(owner_id=UserId(1), type=ReminderType.ONE_TIME, due_date=datetime(2026, 1, 1, tzinfo=timezone.utc), person_id=PersonId(1)))` |  |
| `DELETE /v2/reminders/{reminderId}` | supported | `reminders.delete(ReminderIdType(1))` | beta; API 2026-07-15+ |
| `GET /v2/reminders/{reminderId}` | supported | `reminders.get(ReminderIdType(1))` | beta; API 2026-07-15+ |
| `POST /v2/reminders/{reminderId}` | supported | `reminders.update(ReminderIdType(1), ReminderUpdate(content="x"))` | beta; API 2026-09-17+ |
| `POST /v2/semantic-search` | supported | `companies.semantic_search("prompt text")` |  |
| `GET /v2/tasks/company-merges` | supported | `tasks.list_merge_tasks("company")` |  |
| `GET /v2/tasks/company-merges/{taskId}` | supported | `companies.get_merge_status("t1")` |  |
| `GET /v2/tasks/person-merges` | supported | `tasks.list_merge_tasks("person")` |  |
| `GET /v2/tasks/person-merges/{taskId}` | supported | `persons.get_merge_status("t1")` |  |
| `GET /v2/teams` | planned |  | beta; API 2026-09-17+; phase 2.x-1 |
| `GET /v2/teams/{teamId}` | planned |  | beta; API 2026-09-17+; phase 2.x-1 |
| `GET /v2/transcripts` | supported | `transcripts.list()` |  |
| `DELETE /v2/transcripts/{transcriptId}` | planned |  | beta; API 2026-09-17+; phase 2.x-1 |
| `GET /v2/transcripts/{transcriptId}` | supported | `transcripts.get(1)` |  |
| `GET /v2/transcripts/{transcriptId}/fragments` | supported | `transcripts.fragments(1)` |  |
| `GET /v2/users` | planned |  | beta; phase 2.x-1 |
| `GET /v2/users/{userId}` | planned |  | beta; phase 2.x-1 |
| `GET /v2/webhooks` | supported | `webhooks.list()` | beta; API 2026-07-15+ |
| `POST /v2/webhooks` | supported | `webhooks.create(WebhookCreate(url="https://example.com/h", subscriptions=[]))` | beta; API 2026-07-15+ |
| `DELETE /v2/webhooks/{webhookId}` | supported | `webhooks.delete(WebhookId(1))` | beta; API 2026-07-15+ |
| `GET /v2/webhooks/{webhookId}` | supported | `webhooks.get(WebhookId(1))` | beta; API 2026-07-15+ |
| `POST /v2/webhooks/{webhookId}` | supported | `webhooks.update(WebhookId(1), WebhookUpdate(status="disabled"))` | beta; API 2026-07-15+ |

## V1 API

40 supported, 19 equivalent

| Operation | Status | SDK | Notes |
|---|---|---|---|
| `GET /auth/whoami` | equivalent |  | via v2 GET /v2/auth/whoami |
| `GET /entity-files` | supported | `files.list(company_id=CompanyId(1))` | one person's, company's or opportunity's files (V2 lists only org-wide: files.feed) |
| `POST /entity-files` | supported | `files.upload_bytes(b"x", "a.txt", company_id=CompanyId(1))` |  |
| `GET /entity-files/download/{}` | supported | `files.download(FileId(1))` |  |
| `GET /entity-files/{}` | supported | `files.get(FileId(1))` | the file's record, with its person/company/opportunity, under any API version pin; get_download_url uses V2 when it can |
| `GET /field-value-changes` | supported | `field_value_changes.list(FieldId(1), company_id=CompanyId(1))` |  |
| `GET /field-values` | supported | `field_values.list(company_id=CompanyId(1))` |  |
| `POST /field-values` | supported | `field_values.create(FieldValueCreate(field_id=FieldId(1), entity_id=1, value="x"))` |  |
| `DELETE /field-values/{}` | supported | `field_values.delete(FieldValueId(1))` |  |
| `PUT /field-values/{}` | supported | `field_values.update(FieldValueId(1), "x")` |  |
| `GET /fields` | supported | `fields.list()` |  |
| `POST /fields` | supported | `fields.create(FieldCreate(name="x", entity_type=EntityType.PERSON, value_type=FieldValueType.TEXT))` |  |
| `DELETE /fields/{}` | supported | `fields.delete(FieldId(1))` |  |
| `GET /interactions` | supported | `interactions.list(type=InteractionType.MEETING, person_id=PersonId(1), start_time=datetime(2024, 1, 1, tzinfo=timezone.utc), end_time=datetime(2024, 1, 2, tzinfo=timezone.utc))` |  |
| `POST /interactions` | supported | `interactions.create(InteractionCreate(type=InteractionType.MEETING, person_ids=[PersonId(1)], content="x", date="2024-01-01T00:00:00Z"))` |  |
| `DELETE /interactions/{}` | supported | `interactions.delete(InteractionId(1), InteractionType.MEETING)` |  |
| `GET /interactions/{}` | supported | `interactions.get(InteractionId(1), InteractionType.MEETING)` |  |
| `PUT /interactions/{}` | supported | `interactions.update(InteractionId(1), InteractionType.MEETING, InteractionUpdate(content="x"))` |  |
| `GET /lists` | equivalent |  | via v2 GET /v2/lists |
| `POST /lists` | supported | `lists.create(ListCreate(name="x", type=ListType.PERSON, is_public=False, owner_id=UserId(1)))` | only for a list created with an owner or extra permissions (V2 can't set them) |
| `GET /lists/{}` | supported | `lists.get_size(ListId(1), force=True)` | for the list size only (V2 has none); the list itself is read from V2 |
| `GET /lists/{}/list-entries` | equivalent |  | via v2 GET /v2/lists/{listId}/list-entries |
| `POST /lists/{}/list-entries` | supported | `lists.entries(ListId(1)).add_person(PersonId(1))` |  |
| `DELETE /lists/{}/list-entries/{}` | supported | `lists.entries(ListId(1)).delete(ListEntryId(1))` |  |
| `GET /lists/{}/list-entries/{}` | equivalent |  | via v2 GET /v2/lists/{listId}/list-entries/{listEntryId} |
| `GET /notes` | supported | `notes.list()` |  |
| `POST /notes` | equivalent |  | via v2 POST /v2/notes |
| `DELETE /notes/{}` | equivalent |  | via v2 DELETE /v2/notes/{noteId} |
| `GET /notes/{}` | equivalent |  | via v2 GET /v2/notes/{noteId} |
| `PUT /notes/{}` | equivalent |  | via v2 POST /v2/notes/{noteId} |
| `GET /opportunities` | supported | `opportunities.search("x")` |  |
| `POST /opportunities` | supported | `opportunities.create(OpportunityCreate(name="x", list_id=ListId(1)))` |  |
| `DELETE /opportunities/{}` | supported | `opportunities.delete(OpportunityId(1))` |  |
| `GET /opportunities/{}` | supported | `opportunities.get_details(OpportunityId(1))` |  |
| `PUT /opportunities/{}` | supported | `opportunities.update(OpportunityId(1), OpportunityUpdate(name="x"))` |  |
| `GET /organizations` | supported | `companies.search("x")` |  |
| `POST /organizations` | supported | `companies.create(CompanyCreate(name="x"), if_not_exists=False)` |  |
| `GET /organizations/fields` | equivalent |  | via v2 GET /v2/companies/fields |
| `DELETE /organizations/{}` | supported | `companies.delete(CompanyId(1))` |  |
| `GET /organizations/{}` | supported | `companies.get_associated_person_ids(CompanyId(1))` |  |
| `PUT /organizations/{}` | supported | `companies.update(CompanyId(1), CompanyUpdate(name="x"))` |  |
| `GET /persons` | supported | `persons.search("x")` |  |
| `POST /persons` | supported | `persons.create(PersonCreate(first_name="a", last_name="b"), if_not_exists=False)` |  |
| `GET /persons/fields` | equivalent |  | via v2 GET /v2/persons/fields |
| `DELETE /persons/{}` | supported | `persons.delete(PersonId(1))` |  |
| `GET /persons/{}` | supported | `persons.get_associated_company_ids(PersonId(1))` |  |
| `PUT /persons/{}` | supported | `persons.update(PersonId(1), PersonUpdate(first_name="a"))` |  |
| `GET /rate-limit` | equivalent |  | via v2 GET /v2/rate-limit |
| `GET /relationships-strengths` | supported | `relationships.get(PersonId(1))` |  |
| `GET /reminders` | equivalent |  | via v2 GET /v2/reminders |
| `POST /reminders` | equivalent |  | via v2 POST /v2/reminders |
| `DELETE /reminders/{}` | supported | `reminders.delete(ReminderIdType(1))` | only as the retry when V2 refuses (V2: creator or owner only) |
| `GET /reminders/{}` | equivalent |  | via v2 GET /v2/reminders/{reminderId} |
| `PUT /reminders/{}` | supported | `reminders.update(ReminderIdType(1), ReminderUpdate(type=ReminderType.RECURRING, reset_type=ReminderResetType.EMAIL, reminder_days=30))` | to change a reminder's type (V2 can't), and as the retry when V2 refuses an update (V2: creator or owner only) |
| `GET /webhook` | equivalent |  | via v2 GET /v2/webhooks |
| `POST /webhook/subscribe` | equivalent |  | via v2 POST /v2/webhooks |
| `DELETE /webhook/{}` | equivalent |  | via v2 DELETE /v2/webhooks/{webhookId} |
| `GET /webhook/{}` | equivalent |  | via v2 GET /v2/webhooks/{webhookId} |
| `PUT /webhook/{}` | equivalent |  | via v2 POST /v2/webhooks/{webhookId} |
