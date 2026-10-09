import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: splash
    readonly property int brandSize: Math.min(420, Math.max(170, Math.min(width * 0.23, height * 0.32)))
    Rectangle { anchors.fill: parent; color: "#0B1120" }
    ColumnLayout {
        anchors.centerIn: parent
        spacing: 24
        RowLayout {
            Layout.alignment: Qt.AlignHCenter; spacing: 22
            Avatar { diameter: Math.round(splash.brandSize * 0.65) }
            Rectangle { width: 1; height: splash.brandSize * 0.7; color: "#455C73" }
            BrandLogo { Layout.preferredWidth: splash.brandSize; Layout.preferredHeight: splash.brandSize }
        }
        Text { text: "GAMEGRID"; color: "#F4F7FB"; font.pixelSize: 30; font.bold: true; Layout.alignment: Qt.AlignHCenter }
        Text { text: "Powered by StickForYou"; color: "#64BCC1"; font.pixelSize: 15; Layout.alignment: Qt.AlignHCenter }
        Text { text: bridge.statusText; color: "#A8B8CA"; font.pixelSize: 16; Layout.alignment: Qt.AlignHCenter }
        BusyIndicator { running: bridge.busy; Layout.alignment: Qt.AlignHCenter }
        ActionButton { text: "Retry"; visible: !bridge.busy; Layout.alignment: Qt.AlignHCenter; onClicked: bridge.begin() }
    }
}
