import sys

from PyQt5.QtWidgets import *

from gui.ExtractVariablesGUI import ExtractVariablesGUI
from gui.ComputeVolumeGUI import ComputeVolumeGUI
from gui.CompareResultsGUI import CompareResultsGUI


class MyMainWindow(QWidget):
    def __init__(self):
        super().__init__()

        slf = ExtractVariablesGUI()
        volume = ComputeVolumeGUI()
        compare = CompareResultsGUI()

        stackLayout = QStackedLayout()
        stackLayout.addWidget(QLabel('Hello! This is the start page (TODO)'))
        stackLayout.addWidget(slf)
        stackLayout.addWidget(volume)
        stackLayout.addWidget(compare)

        pageList = QListWidget()
        pageList.setFixedWidth(200)
        for name in ['Start', 'Extract variables', 'Compute volumes', 'Compare two results']:
            pageList.addItem('\n' + name + '\n')
        pageList.setFlow(QListView.TopToBottom)
        pageList.currentRowChanged.connect(stackLayout.setCurrentIndex)
        pageList.setCurrentRow(0)


        vline = QFrame()
        vline.setFrameShape(QFrame.VLine)

        mainLayout = QHBoxLayout()
        mainLayout.addWidget(pageList)
        mainLayout.addWidget(vline)
        mainLayout.addLayout(stackLayout)

        self.setLayout(mainLayout)
        self.setWindowTitle('Main window')
        self.resize(300, 300)

        self.frameGeom = self.frameGeometry()
        self.move(self.frameGeom.center())
        self.show()


def exception_hook(exctype, value, traceback):
    """!
    @brief Needed for supressing traceback silencing in newer vesion of PyQt5
    """
    sys._excepthook(exctype, value, traceback)
    sys.exit(1)


if __name__ == '__main__':
    # suppress explicitly traceback silencing
    sys._excepthook = sys.excepthook
    sys.excepthook = exception_hook

    app = QApplication(sys.argv)
    window = MyMainWindow()
    app.exec_()

