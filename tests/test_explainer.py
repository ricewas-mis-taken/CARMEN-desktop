import score_store
import vacation
from qt_ui import explainer, home_tab


def test_the_sheet_opens_on_either_page_and_draws_without_errors(qtbot, isolate_state):
    for page, index in (("score", 0), ("vacation", 1)):
        sheet = explainer.Explainer(page)
        qtbot.addWidget(sheet)
        sheet.show()
        assert sheet._stack.currentIndex() == index and sheet._tabs[index].isChecked()
        sheet.grab()                                         # paints every drawing on the page
        sheet._tabs[1 - index].click()
        assert sheet._stack.currentIndex() == 1 - index
        sheet.grab()


def test_the_i_on_the_score_opens_the_sheet(qtbot, isolate_state):
    info = explainer.InfoButton("vacation")
    qtbot.addWidget(info)
    info.click()
    sheet = explainer._OPEN[-1]
    qtbot.addWidget(sheet)
    assert sheet._stack.currentIndex() == 1
    sheet.close()
    assert sheet not in explainer._OPEN


def test_the_sheet_quotes_the_real_numbers():
    assert vacation.most_a_day(60) == 44.5 and score_store.OVER_BONUS_MAX == 8
