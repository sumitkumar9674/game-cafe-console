import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    Rectangle {
        anchors.fill: parent
        radius: 13
        color: "#ec132235"
        border.color: "#35c7c7"
        border.width: 1
    }
    DragHandler {
        target: null
        onActiveChanged: {
            if (active) root.startSystemMove()
            else bridge.clampCompactTimer()
        }
    }
    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10
        Text { text: "◷"; color: "#35c7c7"; font.pixelSize: 24 }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1
            Text { objectName: "compactTimeText"; text: bridge.view.timeText || "00:00:00"; color: "#f6fbff"; font.pixelSize: 21; font.bold: true }
            Text { objectName: "compactPhaseText"; text: bridge.view.phase || "SESSION"; color: bridge.view.phase === "PAUSED" ? "#f0b66c" : "#7fded5"; font.pixelSize: 10; font.bold: true }
        }
        ActionButton {
            objectName: "expandCompactTimer"
            text: "Expand"
            secondary: true
            onClicked: bridge.expandWidget()
        }
    }
}
