"""Fenêtre « Strava / Nolio… » : appli API de l'utilisateur, connexion OAuth, envoi automatique.

La connexion ouvre la page d'accord du service dans le navigateur ; la réponse
revient sur http://localhost:8765, attendue dans un fil à part pour ne pas
figer la fenêtre (bouton Annuler). Avec une URL de rappel en https (que
l'appli ne peut pas recevoir), on recopie l'adresse de la page affichée après
l'accord.
"""

from __future__ import annotations

import threading
import webbrowser

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from ..sync import (AccountBook, Outbox, SyncError, auto_services, finish_connect, open_service, parse_callback,
                    start_connect)
from ..sync.web import wait_for_code

HELP = {
    "strava": ('Créez votre appli sur <a href="https://www.strava.com/settings/api">strava.com/settings/api</a> '
               "(domaine de rappel : <b>localhost</b>), puis copiez ici son Client ID et son Client Secret."),
    "nolio": ('Créez votre appli « personnelle » sur <a href="https://www.nolio.io/api/">nolio.io/api</a> '
              "et recopiez ici son identifiant, son secret et son URL de rappel, au caractère près "
              "(<b>http://localhost:8765/nolio</b> conseillée ; si Nolio exige du https, "
              "<b>https://localhost/nolio</b> : il faudra alors recopier l'adresse de la page après l'accord)."),
}
REDIRECT_FIELD = {"nolio"}  # services dont l'URL de rappel se saisit (Strava n'en demande qu'un domaine)
PASTE_PROMPT = ("Après avoir accepté, le navigateur affiche une page d'erreur ou blanche : c'est normal.\n"
                "Copiez toute son adresse (barre d'adresse, elle contient « code= ») et collez-la ici :")


class _Signals(QObject):
    connected = Signal(str, str)  # service, erreur ("" si réussi)


class ServiceBox(QGroupBox):
    def __init__(self, key: str, dialog: AccountsDialog) -> None:
        self.key = key
        self.dialog = dialog
        self.account = dialog.book[key]
        self.service = open_service(key, self.account)
        super().__init__(self.service.label)
        layout = QVBoxLayout(self)
        help_label = QLabel(HELP[key])
        help_label.setWordWrap(True)
        help_label.setOpenExternalLinks(True)
        help_label.setTextFormat(Qt.RichText)
        layout.addWidget(help_label)
        form = QFormLayout()
        self.client_id = QLineEdit(self.account.client_id)
        self.client_secret = QLineEdit(self.account.client_secret)
        self.client_secret.setEchoMode(QLineEdit.Password)
        form.addRow("Identifiant (client ID)", self.client_id)
        form.addRow("Secret (client secret)", self.client_secret)
        self.redirect: QLineEdit | None = None
        if key in REDIRECT_FIELD:
            self.redirect = QLineEdit(self.account.redirect_uri)
            self.redirect.setPlaceholderText(self.service.default_redirect_uri())
            self.redirect.setToolTip("Exactement l'URL de rappel déclarée dans votre appli API")
            form.addRow("URL de rappel", self.redirect)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        row.addWidget(self.state_label, 1)
        self.connect_button = QPushButton()
        self.connect_button.clicked.connect(self._connect_clicked)
        row.addWidget(self.connect_button)
        layout.addLayout(row)
        self.auto = QCheckBox("Envoyer chaque sortie dès qu'elle est terminée")
        self.auto.setChecked(self.account.auto)
        self.auto.toggled.connect(self._auto_toggled)
        layout.addWidget(self.auto)
        self.busy = False
        self.cancel: threading.Event | None = None
        self.refresh()

    def refresh(self, error: str = "") -> None:
        a = self.account
        if self.busy:
            text = "En attente de votre accord dans le navigateur…"
        elif error:
            text = error
        elif a.connected:
            text = f"Connecté{f' : {a.athlete}' if a.athlete else ''}"
        else:
            text = "Non connecté"
        self.state_label.setText(text)
        self.state_label.setStyleSheet("color: #e5484d;" if error else "")
        self.connect_button.setText("Annuler" if self.busy else "Déconnecter" if a.connected else "Se connecter")

    def _store_credentials(self) -> None:
        cid, secret = self.client_id.text().strip(), self.client_secret.text().strip()
        redirect = self.redirect.text().strip() if self.redirect is not None else self.account.redirect_uri
        if (cid, secret, redirect) != (self.account.client_id, self.account.client_secret,
                                       self.account.redirect_uri):
            self.account.client_id, self.account.client_secret = cid, secret
            self.account.redirect_uri = redirect
            self.account.disconnect()  # autre appli : les jetons de l'ancienne ne valent plus
        self.dialog.book.save()

    def _auto_toggled(self, on: bool) -> None:
        self.account.auto = on
        self.dialog.book.save()

    def _connect_clicked(self) -> None:
        if self.busy:  # Annuler : on cesse d'attendre la réponse du navigateur
            if self.cancel is not None:
                self.cancel.set()
            return
        self._store_credentials()
        if self.account.connected:
            self.account.disconnect()
            self.dialog.book.save()
            self.refresh()
            return
        if not self.account.configured:
            self.refresh("Saisissez d'abord l'identifiant et le secret de votre appli.")
            return
        try:
            auth = start_connect(self.service)
        except SyncError as e:
            self.refresh(str(e))
            return
        code = None
        if auth.local is None:  # URL de rappel en https : l'adresse de retour se recopie
            webbrowser.open(auth.url)
            pasted, ok = QInputDialog.getText(self, f"Connexion à {self.service.label}", PASTE_PROMPT)
            if not ok or not pasted.strip():
                self.refresh()
                return
            try:
                code = parse_callback(pasted, auth.state)
            except SyncError as e:
                self.refresh(str(e))
                return
        self.busy = True
        self.cancel = cancel = threading.Event()
        self.refresh()
        signals = self.dialog.signals

        def work() -> None:
            try:
                got = code
                if got is None:
                    port, path = auth.local
                    got = wait_for_code(path, auth.state, port, ready=lambda: webbrowser.open(auth.url),
                                        cancel=cancel)
                finish_connect(self.service, self.dialog.book, auth, got)
                error = ""
            except SyncError as e:
                error = str(e)
            try:
                signals.connected.emit(self.key, error)
            except RuntimeError:  # fenêtre fermée entre-temps : les jetons sont tout de même gardés
                pass

        threading.Thread(target=work, daemon=True, name=f"connexion {self.key}").start()

    def connected(self, error: str) -> None:
        self.busy = False
        self.cancel = None
        self.refresh("" if error == "connexion annulée" else error)


class AccountsDialog(QDialog):
    """Comptes Strava et Nolio ; `send_requested` demande l'envoi des sorties en attente."""

    send_requested = Signal()

    def __init__(self, book: AccountBook, outbox: Outbox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Strava et Nolio")
        self.setMinimumWidth(560)
        self.book = book
        self.outbox = outbox
        self.signals = _Signals(self)
        self.signals.connected.connect(self._connected)
        layout = QVBoxLayout(self)
        intro = QLabel("Chaque sortie (séance, mode libre, pente) est enregistrée en .fit à la fin, "
                       "puis envoyée aux comptes connectés. Un envoi raté est retenté au lancement suivant.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.boxes = {key: ServiceBox(key, self) for key in ("strava", "nolio")}
        for box in self.boxes.values():
            layout.addWidget(box)
        row = QHBoxLayout()
        folder = QPushButton("Ouvrir le dossier des sorties")
        folder.clicked.connect(self._open_folder)
        row.addWidget(folder)
        self.send_button = QPushButton()
        self.send_button.clicked.connect(self._send)
        row.addWidget(self.send_button)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_pending()

    def _connected(self, key: str, error: str) -> None:
        self.boxes[key].connected(error)
        self._refresh_pending()
        if not error:
            self.send_requested.emit()  # les sorties en attente partent aussitôt

    def _refresh_pending(self) -> None:
        n = len({name for name, _ in self.outbox.pending(auto_services(self.book))})
        self.send_button.setText(f"Envoyer les sorties en attente ({n})")
        self.send_button.setEnabled(n > 0)

    def _send(self) -> None:
        self.send_requested.emit()
        self.send_button.setEnabled(False)

    def _open_folder(self) -> None:
        self.outbox.dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.outbox.dir)))

    def done(self, result: int) -> None:  # noqa: D401 (fermeture : on garde ce qui a été saisi)
        for box in self.boxes.values():
            if box.busy and box.cancel is not None:
                box.cancel.set()  # libère le port d'écoute
            elif not box.busy:
                box._store_credentials()
        super().done(result)
