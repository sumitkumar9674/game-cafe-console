import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property string staffPanel: ""
    Rectangle { anchors.fill: parent; color: "#101c2b" }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 36; spacing: 15
        RowLayout { Layout.fillWidth: true
            Avatar { diameter: 48 }
            ColumnLayout { Layout.fillWidth: true
                Text { text: bridge.view.cafeName || "Game Cafe Console"; color: "#f5fbfd"; font.pixelSize: 18; font.bold: true }
                Text { text: bridge.view.ownName || "User PC"; color: "#91a7ba" }
            }
            Text { text: bridge.view.connectionNote || "LOCAL CAFE"; color: "#8fb0bf"; font.pixelSize: 12 }
        }
        Item { Layout.fillHeight: true }
        Panel {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Math.min(page.width - 72, 650)
            implicitHeight: consoleBody.implicitHeight + 54
            ColumnLayout {
                id: consoleBody; anchors.fill: parent; anchors.margins: 27; spacing: 18
                Text { text: bridge.view.accessAllowed ? "YOUR SESSION IS ACTIVE" : "THIS PC IS LOCKED"; color: bridge.view.accessAllowed ? "#51d9bc" : "#f0b66c"; font.bold: true; font.pixelSize: 13 }
                Text { text: bridge.view.player || "Guest"; color: "#f7fbfd"; font.pixelSize: 29; font.bold: true }
                Text { text: bridge.view.phase || "WAITING"; color: "#96aec0"; font.pixelSize: 14 }
                Text { text: bridge.view.timeText || "00:00:00"; color: "#35c7c7"; font.pixelSize: 53; font.bold: true; visible: bridge.view.hasSession }
                Text { text: bridge.view.feedback || "Ask staff to start or renew your session."; color: "#a8c0ce"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                RowLayout { Layout.fillWidth: true; spacing: 10
                    ActionButton { text: "Open desktop"; visible: bridge.view.accessAllowed; onClicked: bridge.goDesktop() }
                    ActionButton { text: bridge.view.requestCooldown > 0 ? "Wait " + bridge.view.requestCooldown + "s" : (bridge.view.feedback || "").startsWith("Request failed") ? "Retry request" : "Request unlock"; visible: !bridge.view.accessAllowed; enabled: bridge.view.requestCooldown <= 0 && !bridge.busy; onClicked: bridge.requestUnlock() }
                }
                ActionButton { text: "End session"; danger: true; enabled: bridge.view.hasSession && !bridge.busy; onClicked: root.ask("End session", "End your current session and lock this PC?", "End session", function(){bridge.customerEnd()}) }
                RowLayout { visible: bridge.view.hasSession
                    TextField { id: playerName; placeholderText: "Player name"; Layout.fillWidth: true }
                    ActionButton { text: "Update name"; secondary: true; onClicked: bridge.renamePlayer(playerName.text) }
                }
            }
        }
        Item { Layout.fillHeight: true }
        RowLayout { Layout.alignment: Qt.AlignHCenter
            ActionButton { text: "Staff access"; secondary: true; onClicked: page.staffPanel = page.staffPanel ? "" : "staff" }
        }
        Panel { Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: Math.min(page.width - 72, 650); implicitHeight: staffBody.implicitHeight + 36; visible: page.staffPanel === "staff"
            ColumnLayout { id: staffBody; anchors.fill: parent; anchors.margins: 18; spacing: 12
                Text { text: "Staff access · Admin password required"; color: "#f3f9fb" }
                RowLayout { Layout.fillWidth: true
                    TextField { id: staffPassword; objectName: "staffPasswordField"; Layout.fillWidth: true; echoMode: showPassword.checked ? TextInput.Normal : TextInput.Password; placeholderText: "Admin password" }
                    CheckBox { id: showPassword; objectName: "showStaffPassword"; text: "Show"; checked: false }
                }
                ActionButton { text: "Switch to Admin"; Layout.fillWidth: true; enabled: bridge.view.canSwitchAdmin && !bridge.busy; onClicked: bridge.switchAdmin(staffPassword.text) }
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#395064" }
                ActionButton { text: "Close software"; Layout.fillWidth: true; danger: true; onClicked: root.ask("Close software", "Close Game Cafe Console on this PC? An active session will be finalized and the Windows desktop restored.", "Close", function(){bridge.closeSoftware(staffPassword.text)}) }
            }
        }
        Text { text: "Developed by Sumit Kumar · StickForYou"; color: "#7892a4"; font.pixelSize: 11; Layout.alignment: Qt.AlignHCenter }
    }
}
