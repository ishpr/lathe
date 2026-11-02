# Epic: Build notification microservice

We need to build a new notifications microservice so patients get appointment reminders. Right now the front desk calls people by hand the day before, and we still get around 2,300 missed-appointment tickets a month across the 12 clinics. The team is 4 developers. Marketing wants it to be configurable so they can reuse it for campaigns later.

Stories:
- Set up the notifications repo and CI
- Design the database schema for notifications
- Build the REST API
- Build the admin UI for templates
- Integrate the SMS provider
- Write unit tests
- Deploy to production

Success criteria: patients are reminded, fewer no-shows, better patient experience.
