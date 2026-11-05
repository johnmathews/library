# MCP connectors checked live: claude.ai, ChatGPT and Claude Code

> Purpose
>
> What it took to reach the MCP server from the hosted clients through
> Cloudflare (work unit W13, findings F1 and F14), what each client was seen
> doing, and what is left as follow-up. This is the last unit of the MCP
> connectors plan.

**Date:** 2026-11-05
**Branch:** `eng-mcp-live-docs`

## 1. Context

Every in-repo MCP test runs in-process. Cloudflare Access, the tunnel and
the vendors' OAuth clients exist only in production, so the claim "each
client connects as its own Library user" could only be settled there. The
deployed build was `120d3f6` (#207). The host's `.env` had gained
`LIBRARY_TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP` earlier the same day
through the home-server role, so the login and registration limits count
each outside client separately rather than all of them as the tunnel.

## 2. The proxy

- **Four bypass paths, not eight.** The plan listed each discovery
  document and its `/*` form. The Access dashboard took only five
  destinations per application, so the bypass is `/mcp`, `/mcp/*`,
  `/.well-known` and `/.well-known/*`. Library serves nothing else under
  `/.well-known` (a probe of two other paths got JSON `404`s), so the
  folder exposes no more than the eight paths did. `docs/mcp.md` §1.6 now
  gives the four-path form.
- **Bypass, not Allow.** The application's one policy is *Bypass* for
  Everyone. An *Allow* policy for Everyone would still send claude.ai's
  servers to the Access login.
- **Browser Integrity Check.** The tracker app's first claude.ai connect
  was challenged by it, so a WAF custom rule skips it for Anthropic's
  outbound ranges on Library's MCP paths. Whether Library would have been
  challenged without the rule was not measured. ChatGPT needed no rule for
  the tracker and none was added here.
- **Checked from outside,** with no Access cookie: the discovery
  documents return JSON naming the public `https://` issuer and endpoints;
  a `POST /mcp` with no token gets `401` and `resource_metadata`;
  registration with a redirect URI off the allow-list gets `400`; `/`,
  `/api/auth/login` and `/oauth/consent` still go to the Access login.

## 3. The clients

- **claude.ai** registered, showed the consent page, and made searches,
  `get_library_context`, `money_query` (reporting coverage), `get_document`
  and `get_document_file` calls, all as the approving user. An
  `update_document_metadata` preview was applied after the user said yes,
  and the audit trail recorded `edited_by: mcp` with that user's id. The
  `mcp_call` log lines and `library_mcp_calls_total` agreed call for call.
- **ChatGPT** connected for both household members, each as their own
  user, and its searches ran as the approver.
- **Claude Code** registered as its own client and its calls ran as the
  approver.

## 4. F14, settled

- claude.ai shows an `image` block to the user as well as the model. The
  model still told the user it could not show the photo, and offered the
  web link.
- Asked to show a PDF, the model did not call `get_document_file` at all
  and offered the text.
- A chat attachment cannot reach `ingest_document`. The model would have
  to copy the file out as base64 inside the call (a 68 KB PDF is about
  91,000 characters). It declined and offered the web upload or a note.
  This is how the hosted clients pass files to tools, not something
  Library can change.

## 5. Also seen

- A new client session's first `POST /mcp` can get a `400`, and the next
  request succeeds. Both claude.ai and ChatGPT did it; nobody saw an
  error. Not investigated further.
- Whether the model asks before applying an edit is its own choice. A
  preview plus an explicit "remove that tag" was applied three seconds
  later with no question. Library guarantees the edit matches a fresh
  preview, not that a person read it.

## 6. Follow-ups (not in this run)

- Tool descriptions: tell the model that claude.ai shows images to the
  user, to call `get_document_file` for any "show me the file", to give
  `download_url` for files it cannot show, and, in `ingest_document`, to
  point chat attachments at the web upload or mail.
- Edit confirmation: say in `update_document_metadata`'s description
  when to confirm (whenever the model chose the change), or try MCP
  elicitation where clients support it.
- Explain the first-request `400`.
