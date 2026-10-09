import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
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
                    Avatar { diameter: 42 }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text { text: bridge.view.cafeName || "Game Cafe Console"; color: "#f6fbff"; font.bold: true; font.pixelSize: 16; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                        Text { text: bridge.view.ownName || "User PC"; color: "#91a9ba"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                    }
                }
                Text { text: (bridge.view.phase || "WAITING") + " · " + (bridge.view.timeText || "00:00:00"); color: "#35c7c7"; font.pixelSize: 19; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                Text { text: "Player: " + (bridge.view.player || "Guest"); color: "#f5fbff"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                Text { id: connectionMessage; objectName: "widgetConnectionMessage"; text: bridge.view.connectionNote || "Local cafe connection"; color: "#a5bbca"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: connectionMessage.implicitHeight; Layout.preferredHeight: connectionMessage.implicitHeight }
                RowLayout {
                    Layout.fillWidth: true
                    TextField { id: playerName; objectName: "widgetPlayerName"; placeholderText: "Player name"; Layout.fillWidth: true; Layout.minimumWidth: 220 }
                    ActionButton { objectName: "widgetUpdateButton"; text: "Update"; secondary: true; onClicked: bridge.renamePlayer(playerName.text) }
                }
                ActionButton { objectName: "widgetOpenConsoleButton"; text: "Open User Console"; Layout.fillWidth: true; onClicked: bridge.openConsole() }
                Text { objectName: "widgetFooter"; text: "Developed by Sumit Kumar · StickForYou"; color: "#7892a4"; font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
            }
        }
    }
}
