import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    Theme { id: theme }
    implicitWidth: 480
    implicitHeight: Math.max(400, widgetContent.implicitHeight + 60)
    onImplicitWidthChanged: bridge.updateWidgetSize(Math.ceil(implicitWidth), Math.ceil(implicitHeight))
    onImplicitHeightChanged: bridge.updateWidgetSize(Math.ceil(implicitWidth), Math.ceil(implicitHeight))
    Panel {
        anchors.fill: parent
        anchors.margins: 12
        ScrollView {
            id: widgetScroll
            anchors.fill: parent
            anchors.margins: 18
            contentWidth: availableWidth
            contentHeight: widgetContent.implicitHeight
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: widgetContent
                width: widgetScroll.availableWidth
                height: implicitHeight
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    BrandLogo { Layout.preferredWidth: 64; Layout.preferredHeight: 64 }
                    Avatar { diameter: 42 }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text { text: bridge.view.cafeName || "GameGrid"; color: "#F4F7FB"; font.bold: true; font.pixelSize: 16; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                        Text { text: bridge.view.ownName || "User PC"; color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                    }
                }
                Text { objectName: "widgetSessionTimer"; text: (bridge.view.phase || "WAITING") + " · " + (bridge.view.timeText || "00:00:00"); color: bridge.view.phase === "BUFFER" ? theme.buffer : bridge.view.phase === "PAUSED" ? theme.paused : bridge.view.phase === "GRACE" || bridge.view.phase === "EXPIRED" ? theme.grace : theme.active; font.pixelSize: 19; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                Text { text: "Player: " + (bridge.view.player || "Guest"); color: "#F4F7FB"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                Text { id: connectionMessage; objectName: "widgetConnectionMessage"; text: bridge.view.connectionNote || "Local cafe connection"; color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: connectionMessage.implicitHeight; Layout.preferredHeight: connectionMessage.implicitHeight }
                RowLayout {
                    Layout.fillWidth: true
                    TextField { id: playerName; objectName: "widgetPlayerName"; placeholderText: "Player name"; Layout.fillWidth: true; Layout.minimumWidth: 220 }
                    ActionButton { objectName: "widgetUpdateButton"; text: "Update"; secondary: true; onClicked: bridge.renamePlayer(playerName.text) }
                }
                ActionButton { objectName: "widgetOpenConsoleButton"; text: "Open User Console"; Layout.fillWidth: true; onClicked: bridge.openConsole() }
                Text { objectName: "widgetFooter"; text: "Developed by Sumit Kumar · StickForYou"; color: "#8398AC"; font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
            }
        }
    }
}
