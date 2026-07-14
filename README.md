# M13 GitHub Sourcing

Internal sourcing product for discovering technical founders and researchers from concrete GitHub evidence.

## What it does

- Converts intents such as `biotech AI researcher` into real GitHub repository and user discovery.
- Requires domain evidence before a candidate can qualify.
- Records explainable match reasons, search runs, and per-search deduplication.
- Supports Google OAuth restricted to `@m13.co`, user-owned saved searches, daily scheduling, email notifications, and breakout alerts.

## Local setup

1. Copy `.env.example` to `.env` and populate each secret.
2. Apply `supabase_schema.sql` in the Supabase SQL editor.
3. Add the exact local URI to the Google OAuth client's **Authorized redirect URIs**.
4. Run:

   ```bash
   streamlit run app.py --server.port 8503
   ```

5. Open `http://localhost:8503`.

Google OAuth redirect URIs are exact. `localhost:8502`, `localhost:8503`, and a URL with a trailing slash are different values.

## Production

`render.yaml` defines:

- `github-sourcing-ui`: Streamlit web service with automatic deployment.
- `github-sourcing-daily`: daily scheduled search and notification job.

The production OAuth redirect is `https://github-sourcing.onrender.com` unless `AUTH_REDIRECT_URL` is overridden in Render.

## Security

- The browser never receives a Supabase service credential.
- Direct Supabase access is denied to public roles by the schema.
- The server validates `@m13.co` identities and filters every user-owned query by verified email.
- OAuth uses a CSRF nonce and identity-only scopes.
- Notification recipients are restricted to `@m13.co`; user-controlled HTML is escaped.

## Verification

Run:

```bash
python3 -m pytest -q
```

The suite covers query construction, relevance gating, scoring, pagination, API failures, ownership boundaries, OAuth safety, notification retries, and deduplication.
