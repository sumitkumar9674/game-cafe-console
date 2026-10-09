import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    implicitHeight: widgetContent.implicitHeight + 60
    onImplicitHeightChanged: bridge.updateWidgetHeight(implicitHeight)
    Panel { anchors.fill: parent; anchors.margins: 12
        ScrollView { anchors.fill: parent; anchors.margins: 18; contentWidth: availableWidth; clip: true
        ColumnLayout { id: widgetContent; width: parent.availableWidth; spacing: 10
            RowLayout { Avatar { diameter: 42 }
                ColumnLayout { Layout.fillWidth: true
                    Text { text: bridge.view.cafeName || "Game Cafe Console"; color: "#f6fbff"; font.bold: true; font.pixelSize: 16; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Text { text: bridge.view.ownName || "User PC"; color: "#91a9ba"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                }
                ActionButton { text: "Hide"; secondary: true; onClicked: bridge.hideWidget() }
            }
            Text { text: (bridge.view.phase || "WAITING") + " · " + (bridge.view.timeText || "00:00:00"); color: "#35c7c7"; font.pixelSize: 19; font.bold: true }
            Text { text: "Player: " + (bridge.view.player || "Guest"); color: "#f5fbff" }
            Text { text: bridge.view.connectionNote || "Local cafe connection"; color: "#a5bbca"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout { TextField { id: playerName; placeholderText: "Player name"; Layout.fillWidth: true }
                ActionButton { text: "Update"; secondary: true; onClicked: bridge.renamePlayer(playerName.text) }
            }
            ActionButton { text: "Open User Console"; Layout.fillWidth: true; onClicked: bridge.openConsole() }
            Text { text: "Developed by Sumit Kumar · StickForYou"; color: "#7892a4"; font.pixelSize: 10 }
        }
        }
    }
}
