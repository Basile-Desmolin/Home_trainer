"""Export automatique des sorties vers Strava et Nolio.

    outbox = Outbox()                                    # dossier des sorties
    path = outbox.add(points, "Sweet spot")              # .fit d'activité, en attente d'envoi
    book = AccountBook.load()                            # comptes connectés (profil utilisateur)
    for report in send_pending(outbox, book):            # envoi, retenté plus tard en cas d'échec
        print(report)
"""

from .accounts import Account, AccountBook
from .outbox import Outbox, SendReport, auto_services, connect, send_pending
from .services import Nolio, Strava, UploadResult, open_service
from .web import SyncError

__all__ = ["Account", "AccountBook", "Nolio", "Outbox", "SendReport", "Strava", "SyncError", "UploadResult",
           "auto_services", "connect", "open_service", "send_pending"]
