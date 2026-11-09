"""Étape 8b : l'éditeur garde ce qu'il ne montre pas ; FTP de l'éditeur, --ftp et --weight ;
raccourcis rendus après la case FTP ou Poids (audit, section « Autres »)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.formats import decode_workout  # noqa: E402
from home_trainer.profiles import ProfileBook  # noqa: E402
from home_trainer.ui import editor as editor_module  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.editing import StepFields, fields_from_step, step_from_fields  # noqa: E402
from home_trainer.ui.editor import COL_POWER, WorkoutEditor  # noqa: E402
from home_trainer.ui.session import State  # noqa: E402
from home_trainer.workout import HeartRateTarget, Intensity, PowerTarget, PowerUnit, Step  # noqa: E402

ZWO = b"""<?xml version="1.0" encoding="UTF-8"?>
<workout_file>
    <name>Gorby</name>
    <description>Un classique</description>
    <workout>
        <Warmup Duration="600" PowerLow="0.25" PowerHigh="0.75">
            <textevent timeoffset="10" message="On se chauffe"/>
        </Warmup>
        <SteadyState Duration="300" Power="0.88"/>
        <Cooldown Duration="300" PowerLow="0.7" PowerHigh="0.3"/>
    </workout>
</workout_file>
"""


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_unchanged_step_is_kept_whole_and_changed_one_keeps_intensity_and_notes():
    hr = HeartRateTarget.bpm(150)
    original = Step(600, PowerTarget(60, 60, PowerUnit.FTP_PERCENT), "Échauffement", Intensity.WARMUP,
                    "Tranquille", heart_rate=hr)
    f = fields_from_step(original)
    assert step_from_fields(f, original) is original
    longer = step_from_fields(StepFields("12:00", f.power, f.power_end, f.unit, f.name), original)
    assert (longer.duration_s, longer.intensity, longer.notes, longer.heart_rate) == (720, Intensity.WARMUP,
                                                                                      "Tranquille", hr)
    # La puissance change : la cible de FC du fichier ne va plus avec, elle part ; le reste demeure.
    harder = step_from_fields(StepFields(f.duration, "70", "", f.unit, f.name), original)
    assert (harder.intensity, harder.notes, harder.heart_rate) == (Intensity.WARMUP, "Tranquille", None)


def test_editor_keeps_notes_zones_and_description_of_an_imported_zwo(app):
    """Audit, « L'éditeur perd des informations »."""
    workout = decode_workout(ZWO, ".zwo")
    editor = WorkoutEditor(workout, 250)
    editor.tree.topLevelItem(1).setText(COL_POWER, "90")  # on ne touche qu'à la brique du milieu
    result = editor.workout()
    warmup, steady, cooldown = result.steps
    assert (warmup.intensity, warmup.notes) == (Intensity.WARMUP, "On se chauffe")
    assert cooldown.intensity is Intensity.COOLDOWN and steady.power.low == 90
    assert result.description == "Un classique"
    editor.close()


def _window(app, tmp_path, ftp=250):
    book = ProfileBook(tmp_path / "profils.json")
    profile = book.create("Basile", ftp, 70)
    book.save()
    window = MainWindow(parse_workout("10m@60%"), ftp, profiles=book, profile=profile)
    window.timer.stop()
    return window, book, profile


def test_ftp_tried_in_the_editor_then_closed_is_not_applied(app, tmp_path, monkeypatch):
    """Audit, « FTP modifiée à votre insu » : « Fermer » appliquait quand même la FTP au profil."""
    window, book, profile = _window(app, tmp_path)

    def try_ftp_then(result):
        def exec_(editor):
            editor.ftp_box.setValue(300)
            return result
        return exec_

    monkeypatch.setattr(editor_module.WorkoutEditor, "exec", try_ftp_then(QtWidgets.QDialog.Rejected))
    window._edit_workout()
    assert (window.ftp, profile.ftp, ProfileBook.load(book.path).profiles[0].ftp) == (250, 250, 250)
    monkeypatch.setattr(editor_module.WorkoutEditor, "exec", try_ftp_then(QtWidgets.QDialog.Accepted))
    window._edit_workout()
    assert (window.ftp, profile.ftp, ProfileBook.load(book.path).profiles[0].ftp) == (300, 300, 300)
    window.close()


def test_command_line_ftp_and_weight_are_for_this_session_only(app, tmp_path):
    window, book, profile = _window(app, tmp_path)
    window.set_session_ftp_weight(280, 75.5)
    assert (window.ftp, window.free.rider_kg) == (280, 75.5)
    saved = ProfileBook.load(book.path).profiles[0]
    assert (profile.ftp, profile.weight_kg, saved.ftp, saved.weight_kg) == (250, 70, 250, 70)
    window.ftp_box.setValue(290)  # changée ensuite à la main : gardée, comme avant
    window._ftp_changed()
    assert ProfileBook.load(book.path).profiles[0].ftp == 290
    window.close()


def test_shortcuts_work_at_launch_and_after_the_ftp_box(app, tmp_path):
    """Audit, « Raccourcis bloqués » : le curseur restait dans la case FTP, Espace n'arrivait plus."""
    window, _book, _profile = _window(app, tmp_path)
    window.show()
    window.activateWindow()
    assert QTest.qWaitForWindowActive(window, 1000)
    focus = QtWidgets.QApplication.focusWidget()
    assert focus not in (window.ftp_box, window.weight_box)  # au lancement, le clavier est aux raccourcis
    QTest.keyClick(focus, Qt.Key_Space)
    assert window.session.state is State.RUNNING
    window.ftp_box.setFocus()
    window.ftp_box.selectAll()
    QTest.keyClicks(window.ftp_box, "260")
    QTest.keyClick(window.ftp_box, Qt.Key_Return)
    assert window.ftp == 260 and QtWidgets.QApplication.focusWidget() is not window.ftp_box
    QTest.keyClick(QtWidgets.QApplication.focusWidget(), Qt.Key_Space)
    assert window.session.state is State.PAUSED
    window.weight_box.setFocus()
    QTest.keyClick(window.weight_box, Qt.Key_Escape)
    assert QtWidgets.QApplication.focusWidget() is not window.weight_box
    window.close()
