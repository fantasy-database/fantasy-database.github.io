/*
 * Fantasy DataBase — FPL read proxy
 *
 * The FPL API sends no Access-Control-Allow-Origin header, so a page on
 * fantasy-database.github.io cannot fetch a squad itself. This sits in the
 * middle: it fetches the FPL endpoint server-side, where CORS does not apply,
 * and hands the result back with the header the browser needs.
 *
 * Runs on Cloudflare Workers' free tier. It holds no data, has no database,
 * and stores nothing — it forwards a read and adds a header.
 *
 * Deliberately narrow, because an open proxy is somebody else's problem
 * waiting to happen:
 *   - GET only.
 *   - Only https://fantasy.premierleague.com/api/... may be fetched.
 *   - Only the pages of this site may call it.
 *   - No cookies or auth headers are forwarded, so it can only ever read the
 *     public parts of the API — the same things anyone can open in a browser.
 */

const ALLOWED_ORIGINS = [
  "https://fantasy-database.github.io",
  "http://localhost:8899",          // local testing; harmless to leave in
];

const API = "https://fantasy.premierleague.com/api/";

export default {
  async fetch(request) {
    const origin = request.headers.get("Origin") || "";
    const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];

    const cors = {
      "Access-Control-Allow-Origin": allowed,
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Vary": "Origin",
    };

    // Browsers ask permission before the real request on some calls.
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }
    if (request.method !== "GET") {
      return json({ error: "Only GET is allowed here." }, 405, cors);
    }

    const target = new URL(request.url).searchParams.get("url");
    if (!target) {
      return json({ error: "Add ?url= with an FPL API address." }, 400, cors);
    }
    if (!target.startsWith(API)) {
      return json({ error: "This proxy only fetches the FPL API." }, 403, cors);
    }

    let upstream;
    try {
      upstream = await fetch(target, {
        method: "GET",
        headers: { "User-Agent": "fantasy-database.github.io" },
        // A minute of caching. One person refreshing their squad a few times
        // should not become several trips to FPL.
        cf: { cacheTtl: 60, cacheEverything: true },
      });
    } catch (e) {
      return json({ error: "Could not reach the FPL API." }, 502, cors);
    }

    if (!upstream.ok) {
      // 404 here almost always means the team ID is wrong, or that gameweek
      // has no picks yet. Say so rather than passing back a bare number.
      return json({
        error: upstream.status === 404
          ? "No team found with that ID for that gameweek."
          : `The FPL API answered ${upstream.status}.`,
      }, upstream.status, cors);
    }

    return new Response(upstream.body, {
      status: 200,
      headers: {
        ...cors,
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=60",
      },
    });
  },
};

function json(body, status, cors) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json; charset=utf-8" },
  });
}
