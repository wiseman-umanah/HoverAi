// ─── Auth: token manager + authenticated requests ────────────────────────────
//
// Single owner of the access/refresh token lifecycle (storage is tokens.rs).
//
// - `access_token()` returns a token valid for at least REFRESH_MARGIN_S,
//   refreshing proactively when it is about to expire. The backend issues
//   15-minute access tokens, so long-running sessions refresh constantly.
// - Refreshes are single-flight: concurrent callers (the /query pipeline, the
//   renderer's api.ts via the `refresh_tokens` command, startup) share one
//   /auth/refresh call. Refresh tokens rotate on every use, so two parallel
//   refreshes with the same token would otherwise log the user out.
// - Tokens are cleared ONLY when the server rejects the refresh (401/403).
//   Network errors and 5xx (e.g. a Render cold start) keep the session.
// - `send_authed()` rebuilds the request from a closure, so it works with
//   multipart bodies, and retries once after a forced refresh on 401.

use std::time::Duration;

use reqwest::{Client, RequestBuilder, Response, StatusCode};
use serde::{Deserialize, Serialize};
use tauri::AppHandle;

use crate::tokens;

const REFRESH_MARGIN_S: i64 = 60;
const REFRESH_TIMEOUT: Duration = Duration::from_secs(15);

static REFRESH_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

#[derive(Debug)]
pub enum AuthError {
    /// No tokens stored — the user must sign in.
    NoSession,
    /// The server rejected the refresh token; tokens have been cleared.
    Rejected,
    /// Couldn't reach the server (or it returned 5xx). Session kept.
    Network(String),
}

impl AuthError {
    pub fn user_message(&self) -> String {
        match self {
            AuthError::NoSession | AuthError::Rejected => {
                "Your session has expired — please sign in again.".into()
            }
            AuthError::Network(_) => {
                "Can't reach the HoverAI server — check your internet connection.".into()
            }
        }
    }
}

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

fn is_fresh(access: &str) -> bool {
    tokens::jwt_expiry(access) > now() + REFRESH_MARGIN_S
}

pub fn has_session(app: &AppHandle) -> bool {
    tokens::get(app).is_some()
}

/// A usable access token, refreshing first if it is (nearly) expired.
pub async fn access_token(app: &AppHandle, client: &Client) -> Result<String, AuthError> {
    let (access, _) = tokens::get(app).ok_or(AuthError::NoSession)?;
    if is_fresh(&access) {
        return Ok(access);
    }
    refresh(app, client, Some(&access)).await
}

/// Refresh the token pair (single-flight).
///
/// `stale` is the access token the caller found unusable. If another caller
/// already replaced it with a fresh one while we waited for the lock, that
/// token is returned without hitting the server.
pub async fn refresh(
    app: &AppHandle,
    client: &Client,
    stale: Option<&str>,
) -> Result<String, AuthError> {
    let _guard = REFRESH_LOCK.lock().await;

    let (access, refresh_token) = tokens::get(app).ok_or(AuthError::NoSession)?;
    if stale.is_some_and(|s| s != access) && is_fresh(&access) {
        return Ok(access);
    }

    #[derive(Serialize)]
    struct Body<'a> {
        refresh_token: &'a str,
    }
    #[derive(Deserialize)]
    struct Pair {
        access_token: String,
        refresh_token: String,
    }

    let resp = client
        .post(format!("{}/auth/refresh", crate::api_base()))
        .json(&Body { refresh_token: &refresh_token })
        .timeout(REFRESH_TIMEOUT)
        .send()
        .await
        .map_err(|e| AuthError::Network(e.to_string()))?;

    match resp.status() {
        s if s.is_success() => {
            let pair: Pair = resp
                .json()
                .await
                .map_err(|e| AuthError::Network(format!("bad refresh response: {e}")))?;
            tokens::store(app, &pair.access_token, &pair.refresh_token);
            log::info!("[auth] access token refreshed");
            Ok(pair.access_token)
        }
        StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN => {
            log::warn!("[auth] refresh rejected ({}) — clearing session", resp.status());
            tokens::clear(app);
            Err(AuthError::Rejected)
        }
        s => Err(AuthError::Network(format!("refresh failed with HTTP {s}"))),
    }
}

/// Outcome of an authenticated request that got a response.
pub enum Authed {
    Response(Response),
    /// Server returned 401 even after a refresh.
    Unauthorized,
}

/// Send a request built by `build(token)`, refreshing proactively and once
/// more after a 401. `build` is called again for the retry, so the body
/// (including multipart forms) is rebuilt rather than re-used.
pub async fn send_authed<F>(app: &AppHandle, client: &Client, build: F) -> Result<Authed, AuthError>
where
    F: Fn(&str) -> RequestBuilder,
{
    let token = access_token(app, client).await?;
    let resp = build(&token)
        .send()
        .await
        .map_err(|e| AuthError::Network(e.to_string()))?;
    if resp.status() != StatusCode::UNAUTHORIZED {
        return Ok(Authed::Response(resp));
    }

    let token = refresh(app, client, Some(&token)).await?;
    let retry = build(&token)
        .send()
        .await
        .map_err(|e| AuthError::Network(e.to_string()))?;
    if retry.status() == StatusCode::UNAUTHORIZED {
        return Ok(Authed::Unauthorized);
    }
    Ok(Authed::Response(retry))
}
