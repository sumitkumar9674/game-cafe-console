import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    Rectangle { anchors.fill: parent; color: "#101c2b" }
    ColumnLayout {
        anchors.centerIn: parent
        spacing: 24
        RowLayout {
            Layout.alignment: Qt.AlignHCenter; spacing: 22
            Avatar { diameter: 86 }
            Rectangle { width: 1; height: 72; color: "#466070" }
            Text { text: "StickForYou"; color: "#f5fbff"; font.pixelSize: 28; font.bold: true }
        }
        Text { text: "GAME CAFE CONSOLE"; color: "#f6fbff"; font.pixelSize: 30; font.bold: true; Layout.alignment: Qt.AlignHCenter }
        Text { text: "Powered by StickForYou"; color: "#35c7c7"; font.pixelSize: 15; Layout.alignment: Qt.AlignHCenter }
        Text { text: bridge.statusText; color: "#89a5b8"; font.pixelSize: 16; Layout.alignment: Qt.AlignHCenter }
        BusyIndicator { running: bridge.busy; Layout.alignment: Qt.AlignHCenter }
        ActionButton { text: "Retry"; visible: !bridge.busy; Layout.alignment: Qt.AlignHCenter; onClicked: bridge.begin() }
    }
}
