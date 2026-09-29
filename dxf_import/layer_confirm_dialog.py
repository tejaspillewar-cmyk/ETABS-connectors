"""
Layer-classification confirmation dialog.

Shows one editable list per element-type bucket, pre-filled with an
auto-detected guess (see layer_classifier.py). The user adds/removes layers
per bucket before extraction runs -- this is how the app copes with not
knowing the project's exact layer-naming convention up front.
"""
from ezdxf.addons.xqt import QtWidgets as qw, QtGui as qg

from .layer_classifier import BUCKET_KEYS

BUCKET_LABELS = {
    "slabs": "Slab Layers",
    "str_walls": "Structural Wall Layers",
    "ns_walls": "Non-Structural Wall Layers",
    "beams": "Beam Layers",
    "columns": "Column Layers",
}


class LayerConfirmDialog(qw.QDialog):
    def __init__(self, buckets: dict[str, list[str]], all_layers: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Confirm Detected Layers")
        self.resize(600, 760)
        self.all_layers = all_layers
        self.lists: dict[str, qw.QListWidget] = {}

        layout = qw.QVBoxLayout(self)

        scroll = qw.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = qw.QWidget()
        scroll_layout = qw.QVBoxLayout(scroll_content)

        for key in BUCKET_KEYS:
            self._add_section(scroll_layout, BUCKET_LABELS[key], key, buckets.get(key, []))

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        btn_box = qw.QDialogButtonBox()
        btn_box.addButton("Proceed", qw.QDialogButtonBox.AcceptRole)
        btn_box.addButton("Cancel", qw.QDialogButtonBox.RejectRole)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _add_section(self, scroll_layout, title, key, initial_layers):
        lbl = qw.QLabel(title)
        font = qg.QFont()
        font.setBold(True)
        font.setPointSize(11)
        lbl.setFont(font)

        header_layout = qw.QHBoxLayout()
        header_layout.addWidget(lbl)

        add_btn = qw.QPushButton("+")
        add_btn.setFixedSize(30, 30)
        rm_btn = qw.QPushButton("-")
        rm_btn.setFixedSize(30, 30)

        header_layout.addStretch()
        header_layout.addWidget(add_btn)
        header_layout.addWidget(rm_btn)
        scroll_layout.addLayout(header_layout)

        list_widget = qw.QListWidget()
        list_widget.addItems(initial_layers)
        list_widget.setFixedHeight(110)
        scroll_layout.addWidget(list_widget)
        self.lists[key] = list_widget

        def on_add():
            layer, ok = qw.QInputDialog.getItem(self, "Add Layer", "Select layer to add:", self.all_layers, 0, False)
            if ok and layer:
                items = [list_widget.item(i).text() for i in range(list_widget.count())]
                if layer not in items:
                    list_widget.addItem(layer)

        def on_rm():
            for item in list_widget.selectedItems():
                list_widget.takeItem(list_widget.row(item))

        add_btn.clicked.connect(on_add)
        rm_btn.clicked.connect(on_rm)

    def get_selections(self) -> dict[str, list[str]]:
        def items(key):
            lw = self.lists[key]
            return [lw.item(i).text() for i in range(lw.count())]
        return {key: items(key) for key in BUCKET_KEYS}
