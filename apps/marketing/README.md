# @acme/marketing

Serves `example.com`. **No user-generated content, ever.** That is the point of the
separation (ADR-0008).

## Public event pages live here, not on the UGC domain

`example.com/events/<slug>`. They are the exception because they are gated behind
organizer verification - a verified email domain or a paid plan - which makes them
curated and low-risk. Nobody pays to host a phishing page, so the paywall doubles as the
anti-spam filter.

This is also where the SEO equity should accumulate. Individual long-tail event pages
rank; a browsable feed does not, which is why the feed stays behind login in the app.

## Content

MDX files in the repository, edited by pull request. **No CMS** (ADR-0019). Revisit only
if non-technical staff need to publish without a deploy.

## Landing pages per vertical

Multiple landing pages targeting different audiences are cheap and worth testing. But a
landing page changes the framing, not what happens after the click. The product still has
to be sharp for one audience: which CRM integration exists and what the first screen after
install does are singular decisions. See the open positioning decision in
`00-context/product-scope.md`.
