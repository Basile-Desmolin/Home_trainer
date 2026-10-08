"""Export automatique des sorties vers Strava et Nolio.

    outbox = Outbox()                                    # dossier des sorties
    path = outbox.add(points, "Sweet spot")              # .fit d'activité, en attente d'envoi
    book = AccountBook.load()                            # comptes connectés (profil utilisateur)
    for report in send_pending(outbox, book):            # envoi, retenté plus tard en cas d'échec
        print(report)
"""

from .accounts import Account, AccountBook
from .outbox import (Authorization, Outbox, SendReport, auto_services, connect, finish_connect, send_pending,
                     start_connect)
from .services import Nolio, Strava, UploadResult, open_service
from .web import SyncError, parse_callback

__all__ = ["Account", "AccountBook", "Authorization", "Nolio", "Outbox", "SendReport", "Strava", "SyncError",
           "UploadResult", "auto_services", "connect", "finish_connect", "open_service", "parse_callback",
           "send_pending", "start_connect"]
