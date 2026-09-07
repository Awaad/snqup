"""The reserved slug list.

Seeded by migration so every environment has it, and checked before any slug is
issued (cards, organizations, event slugs).

Add to it PROACTIVELY. A brand added after someone registers it is a dispute
with a real person who did nothing wrong; added before, it is nothing.

Four categories, kept separate because they have different review rules:

  ROUTES      technical necessity. `/u/settings` must not be a person.
  PLATFORM    impersonating us. `support` and `billing` are how phishing works.
  BRANDS      trademark exposure. Not exhaustive and cannot be - it covers the
              names most likely to be squatted, and the takedown path
              (runbooks/abuse-takedown.md) handles the rest.
  SENSITIVE   roles a stranger would read as authoritative: `police`,
              `emergency`. Not profanity, which is a moderation question rather
              than a reservation one.

DELIBERATELY NOT HERE: a profanity list. Blocklists in this space are famously
bad at their job - they miss creative spelling and they catch Scunthorpe. Slurs
and abuse are handled by report-and-takedown, where a human looks.
"""

# Anything that is or could become a URL path segment on any of our domains.
# A collision here is not a policy problem, it is a routing bug.
ROUTES: frozenset[str] = frozenset(
    {
        "api",
        "v1",
        "v2",
        "auth",
        "oauth",
        "login",
        "logout",
        "signin",
        "signup",
        "register",
        "callback",
        "session",
        "sessions",
        "token",
        "tokens",
        "admin",
        "administrator",
        "root",
        "system",
        "internal",
        "console",
        "dashboard",
        "settings",
        "preferences",
        "account",
        "accounts",
        "profile",
        "profiles",
        "user",
        "users",
        "me",
        "my",
        "card",
        "cards",
        "c",
        "u",
        "e",
        "s",
        "scan",
        "connect",
        "connection",
        "connections",
        "exchange",
        "exchanges",
        "event",
        "events",
        "org",
        "orgs",
        "organization",
        "organizations",
        "team",
        "teams",
        "company",
        "billing",
        "checkout",
        "subscribe",
        "subscription",
        "plans",
        "pricing",
        "upgrade",
        "invoice",
        "invoices",
        "payment",
        "payments",
        "webhook",
        "webhooks",
        "health",
        "status",
        "metrics",
        "debug",
        "static",
        "assets",
        "public",
        "media",
        "files",
        "download",
        "downloads",
        "img",
        "images",
        "css",
        "js",
        "fonts",
        "favicon",
        "docs",
        "doc",
        "help",
        "faq",
        "support",
        "contact",
        "about",
        "blog",
        "news",
        "press",
        "legal",
        "terms",
        "privacy",
        "cookies",
        "dpa",
        "gdpr",
        "search",
        "explore",
        "discover",
        "feed",
        "trending",
        "new",
        "popular",
        "app",
        "apps",
        "mobile",
        "ios",
        "android",
        "desktop",
        "web",
        "www",
        "mail",
        "email",
        "smtp",
        "imap",
        "ftp",
        "cdn",
        "dns",
        "ns",
        "mx",
        "test",
        "testing",
        "staging",
        "dev",
        "development",
        "demo",
        "sandbox",
        "null",
        "undefined",
        "true",
        "false",
        "none",
        "nil",
        "void",
        "error",
        "errors",
        "404",
        "500",
        "robots",
        "sitemap",
        "manifest",
        "well-known",
        "security",
        "abuse",
        "report",
        "reports",
        "moderation",
        "invite",
        "invites",
        "invitation",
        "join",
        "claim",
        "verify",
        "verification",
        "confirm",
        "reset",
        "recover",
        "unsubscribe",
        "wallet",
        "pass",
        "passes",
        "nfc",
        "qr",
        "vcard",
        "contact-card",
    }
)

# Names that let someone impersonate the product itself. The realistic attack:
# a card at `/u/support` asking a scanner to "verify your account".
PLATFORM: frozenset[str] = frozenset(
    {
        "official",
        "staff",
        "moderator",
        "mod",
        "employee",
        "founder",
        "ceo",
        "owner",
        "operator",
        "service",
        "services",
        "noreply",
        "no-reply",
        "postmaster",
        "webmaster",
        "hostmaster",
        "sysadmin",
        "security-team",
        "trust",
        "trustandsafety",
        "safety",
        "compliance",
        "legal-team",
        "notifications",
        "alerts",
        "updates",
        "announcements",
        "verified",
        "verification-team",
        "premium",
        "pro",
        "business",
        "enterprise",
        "partner",
        "partners",
        "sponsor",
        "sponsors",
    }
)

# Trademark exposure. NOT exhaustive and cannot be; this covers the names most
# likely to be squatted or used for phishing. Everything else is takedown.
BRANDS: frozenset[str] = frozenset(
    {
        "apple",
        "google",
        "microsoft",
        "amazon",
        "meta",
        "facebook",
        "instagram",
        "whatsapp",
        "twitter",
        "x",
        "linkedin",
        "tiktok",
        "snapchat",
        "youtube",
        "netflix",
        "spotify",
        "uber",
        "airbnb",
        "tesla",
        "openai",
        "anthropic",
        "paypal",
        "stripe",
        "visa",
        "mastercard",
        "amex",
        "revolut",
        "wise",
        "coinbase",
        "binance",
        "hsbc",
        "barclays",
        "chase",
        "citi",
        "santander",
        "dhl",
        "fedex",
        "ups",
        "usps",
        "royalmail",
        "salesforce",
        "hubspot",
        "slack",
        "zoom",
        "notion",
        "figma",
        "github",
        "gitlab",
        "atlassian",
        "jira",
        "shopify",
        "squarespace",
        "wix",
        "linktree",
        "calendly",
        "eventbrite",
        "meetup",
        "cvent",
        "hopin",
        "popl",
        "hihello",
        "blinq",
        "mobilo",
    }
)

# Roles a stranger would read as authoritative. Someone presenting a card at
# `/u/police` is claiming something, and we should not let them.
SENSITIVE: frozenset[str] = frozenset(
    {
        "police",
        "fbi",
        "cia",
        "nhs",
        "hospital",
        "ambulance",
        "emergency",
        "911",
        "999",
        "112",
        "gov",
        "government",
        "irs",
        "hmrc",
        "tax",
        "bank",
        "banking",
        "insurance",
        "lawyer",
        "attorney",
        "doctor",
        "clinic",
        "pharmacy",
        "vaccine",
        "covid",
        "charity",
        "donate",
        "donation",
        "relief",
    }
)

_CATEGORIES: tuple[tuple[frozenset[str], str], ...] = (
    (ROUTES, "route collision"),
    (PLATFORM, "platform impersonation"),
    (BRANDS, "brand protection"),
    (SENSITIVE, "sensitive role"),
)


def _build() -> dict[str, str]:
    """Assemble the list, refusing to let a slug sit in two categories.

    A duplicate is not harmless: dict merge order decides which reason gets
    stored, so the same slug could report "route collision" or "brand
    protection" depending on where someone added it. That reason is what a user
    sees when their slug is refused and what support reads when they appeal,
    so it has to be deterministic.

    Raising at import means a bad edit fails immediately rather than shipping a
    plausible-looking wrong answer.
    """
    reserved: dict[str, str] = {}
    for slugs, reason in _CATEGORIES:
        for slug in slugs:
            if slug in reserved:
                raise ValueError(
                    f"{slug!r} is in two categories: {reserved[slug]!r} and "
                    f"{reason!r}. Pick one - the reason is user-visible."
                )
            reserved[slug] = reason
    return reserved


RESERVED: dict[str, str] = _build()


def is_reserved(slug: str) -> bool:
    return slug.strip().lower() in RESERVED
