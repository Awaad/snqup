"""Domain verification by DNS TXT record.


WHY TXT AND NOT A THIRD PARTY: publishing a TXT record proves control of the
zone, which is exactly the claim being made. A third-party service would be a
dependency, a cost and another vendor holding a list of our customers'
domains, for a check that is twenty lines.

WHY NOT AN EMAIL TO admin@domain: it proves control of one mailbox, and plenty
of organizations cannot create arbitrary mailboxes on a domain they genuinely
own.

THE TOKEN IS RANDOM PER VERIFICATION and never derived from the domain or the
organization id. A derived token is predictable, and a predictable token means
anyone can work out what to publish and claim a domain they do not control.
"""

import secrets
from dataclasses import dataclass

import dns.asyncresolver
import dns.exception
import structlog

log = structlog.get_logger()

#: Where the record goes. A dedicated subdomain rather than the apex, so
#: verification never collides with SPF, DMARC or another vendor's TXT record -
#: apex TXT records are crowded and a 255-character limit is real.
CHALLENGE_HOST = "_acme-verify"

#: Prefix inside the record, so a zone with several vendors' tokens can be read
#: by a human and each one identified.
CHALLENGE_PREFIX = "acme-verify="

#: Give up after this many consecutive failures. DNS propagation is slow and
#: users publish records late, so this is generous - but a verification that
#: retries forever is one nobody ever fixes.
MAX_CHECK_ATTEMPTS = 30


@dataclass(frozen=True, slots=True)
class ChallengeInstructions:
    host: str
    record_type: str
    value: str

    @property
    def human(self) -> str:
        return f'Add a TXT record at {self.host} with the value "{self.value}"'


def new_challenge_token() -> str:
    """Random, 32 characters of URL-safe entropy."""
    return secrets.token_urlsafe(24)


def instructions_for(domain: str, token: str) -> ChallengeInstructions:
    return ChallengeInstructions(
        host=f"{CHALLENGE_HOST}.{domain}",
        record_type="TXT",
        value=f"{CHALLENGE_PREFIX}{token}",
    )


def normalise_domain(raw: str) -> str:
    """Strip what people paste.

    They will paste `https://www.example.com/`, and treating that as a domain
    produces a lookup that can never succeed and an error that does not explain
    why.
    """
    domain = raw.strip().lower()
    for prefix in ("https://", "http://"):
        domain = domain.removeprefix(prefix)
    domain = domain.split("/")[0].split("?")[0]
    domain = domain.removeprefix("www.")
    return domain.rstrip(".")


async def check_txt_record(domain: str, token: str, *, resolver_timeout: float = 5.0) -> bool:
    """Look up the TXT record and compare.

    Returns False rather than raising on NXDOMAIN or timeout: those are the
    NORMAL states while someone is still publishing the record, and treating
    them as errors would fill the log with noise from users doing nothing
    wrong.

    A resolver failure and a missing record are deliberately indistinguishable
    to the caller. Both mean "not verified yet, try again", and there is no
    action a user could take that differs between them.
    """
    expected = f"{CHALLENGE_PREFIX}{token}"
    host = f"{CHALLENGE_HOST}.{domain}"

    resolver = dns.asyncresolver.Resolver()
    # Named `resolver_timeout`, not `timeout`: ASYNC109 flags the latter and
    # asks for asyncio.timeout, which would be the wrong tool here - dnspython
    # enforces per-query and total deadlines itself, and wrapping it would give
    # two competing timeouts with the outer one cancelling mid-query.
    resolver.lifetime = resolver_timeout
    resolver.timeout = resolver_timeout

    try:
        answers = await resolver.resolve(host, "TXT")
    except (dns.exception.DNSException, OSError) as exc:
        log.info("domain.dns_lookup_failed", host=host, error=type(exc).__name__)
        return False

    for record in answers:
        # A TXT record arrives as one or more quoted strings, and long values
        # are SPLIT across them by the protocol. Joining before comparing is
        # what makes a 32-character token work at all.
        value = b"".join(record.strings).decode("utf-8", "ignore").strip()
        if secrets.compare_digest(value, expected):
            return True

    return False
