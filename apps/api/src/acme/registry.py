"""Model registry. The composition root for ORM metadata.

Imports every domain's models module so Base.metadata is complete. Without it,
Alembic autogenerate and tests/test_model_schema_sync.py compare an incomplete
picture and pass while half the schema is unmapped.

WHY IT LIVES HERE AND NOT IN core/
----------------------------------
This module is not infrastructure. It is a composition root - the one place
whose job is to know about everything so that nothing else has to. Sitting
beside core/ and domains/ rather than inside either is what makes that honest,
and it keeps the layer contract enforceable rather than carved out with an
exception.

Nothing imports this except migrations/env.py and the drift test.
"""

from acme.domains.billing import models as billing_models
from acme.domains.cards import models as cards_models
from acme.domains.connections import models as connections_models
from acme.domains.events import models as events_models
from acme.domains.identity import models as identity_models
from acme.domains.notifications import models as notifications_models
from acme.domains.safety import models as safety_models

__all__ = [
    "billing_models",
    "cards_models",
    "connections_models",
    "events_models",
    "identity_models",
    "notifications_models",
    "safety_models",
]
