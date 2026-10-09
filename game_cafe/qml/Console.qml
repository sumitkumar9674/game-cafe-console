import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property string staffPanel: ""
    function toggleStaffAccess() {
        if (staffPanel === "staff") {
            staffPassword.text = ""
            bridge.lockStaffAccess()
            root.cancelConfirmation()
            staffPanel = ""
        } else {
            bridge.lockStaffAccess()
            staffPassword.text = ""
            staffPanel = "staff"
        }
    }
    Component.onDestruction: bridge.lockStaffAccess()
    Connections {
        target: bridge
        function onStaffAuthChanged() {
            if (bridge.staffAuthorized) staffPassword.text = ""
            else root.cancelConfirmation()
        }
    }
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
            ActionButton { text: page.staffPanel === "staff" ? "Close Staff Access" : "Staff Access"; secondary: true; onClicked: page.toggleStaffAccess() }
        }
        Panel { Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: Math.min(page.width - 72, 650); implicitHeight: staffBody.implicitHeight + 36; visible: page.staffPanel === "staff"
            ColumnLayout { id: staffBody; anchors.fill: parent; anchors.margins: 18; spacing: 12
                Text { text: bridge.staffAuthorized ? "Staff Controls" : "Staff Access · Admin password required"; color: "#f3f9fb"; font.bold: true }
                ColumnLayout {
                    visible: !bridge.staffAuthorized
                    Layout.fillWidth: true
                    PasswordField {
                        id: staffPassword
                        objectName: "staffPasswordField"
                        Layout.fillWidth: true
                        placeholderText: "Admin password"
                        onAccepted: bridge.authenticateStaff(text)
                    }
                    ActionButton { objectName: "staffAuthenticateButton"; text: "Unlock Staff Controls"; Layout.fillWidth: true; enabled: !bridge.busy; onClicked: bridge.authenticateStaff(staffPassword.text) }
                }
                ColumnLayout {
                    objectName: "staffControls"
                    visible: bridge.staffAuthorized
                    Layout.fillWidth: true
                    spacing: 10
                    Text { text: (bridge.view.phase || "WAITING") + " · " + (bridge.view.timeText || "00:00:00"); color: "#35c7c7"; font.bold: true }
                    RowLayout {
                        visible: !bridge.view.hasSession
                        Layout.fillWidth: true
                        ComboBox { id: staffKind; model: ["Timed", "No timer"]; Layout.preferredWidth: 115; onActivated: bridge.touchStaffAccess() }
                        ComboBox { id: staffPaid; model: ["15", "30", "60", "120", "Custom Minutes"]; currentIndex: 2; visible: staffKind.currentIndex === 0; Layout.preferredWidth: 145; onActivated: bridge.touchStaffAccess() }
                        TextField { id: staffCustomPaid; visible: staffKind.currentIndex === 0 && staffPaid.currentIndex === 4; placeholderText: "Minutes"; Layout.preferredWidth: 86; inputMethodHints: Qt.ImhDigitsOnly; onTextEdited: bridge.touchStaffAccess() }
                        TextField { id: staffBuffer; text: "0"; placeholderText: "Buffer"; Layout.preferredWidth: 82; inputMethodHints: Qt.ImhDigitsOnly; onTextEdited: bridge.touchStaffAccess() }
                    }
                    ActionButton {
                        objectName: "staffStartButton"
                        text: "Start / Set Session"
                        visible: !bridge.view.hasSession
                        Layout.fillWidth: true
                        enabled: !bridge.busy
                        onClicked: {
                            bridge.touchStaffAccess()
                            let kind = staffKind.currentIndex === 0 ? "timed" : "open"
                            let paid = kind === "timed" ? (staffPaid.currentIndex === 4 ? staffCustomPaid.text : staffPaid.currentText) : "0"
                            root.ask("Start local session", "Start this " + (kind === "timed" ? paid + " minute timed" : "no-timer") + " session with " + staffBuffer.text + " buffer minutes?", "Start Session", function(){ bridge.staffStartSession(kind, paid, staffBuffer.text) })
                        }
                    }
                    RowLayout {
                        visible: bridge.view.hasSession && bridge.view.kind === "timed"
                        Layout.fillWidth: true
                        ComboBox { id: staffAdd; model: ["1", "2", "5", "15", "30", "60", "Custom Minutes"]; currentIndex: 3; Layout.fillWidth: true; onActivated: bridge.touchStaffAccess() }
                        TextField { id: staffCustomAdd; visible: staffAdd.currentIndex === 6; placeholderText: "Minutes"; Layout.preferredWidth: 86; inputMethodHints: Qt.ImhDigitsOnly; onTextEdited: bridge.touchStaffAccess() }
                        ActionButton {
                            objectName: "staffAddButton"
                            text: "Add Time"
                            enabled: !bridge.busy
                            onClicked: {
                                bridge.touchStaffAccess()
                                let minutes = Number(staffAdd.currentIndex === 6 ? staffCustomAdd.text : staffAdd.currentText)
                                if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
                                    root.ask("Invalid time", "Use a whole number from 1 to 1440 minutes.", "OK", function(){})
                                    return
                                }
                                let preview = bridge.previewStaffAdd(minutes)
                                root.ask("Confirm added time", preview, "Add Time", function(){ bridge.staffAddTime(minutes) })
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#395064" }
                    ActionButton { text: "Switch to Admin"; Layout.fillWidth: true; enabled: bridge.view.canSwitchAdmin && !bridge.view.hasSession && !bridge.busy; onClicked: bridge.switchAdmin() }
                    ActionButton { text: "Close Software"; Layout.fillWidth: true; danger: true; enabled: !bridge.busy; onClicked: root.ask("Close software", "Close Game Cafe Console on this PC? An active session will be finalized and the Windows desktop restored.", "Close", function(){bridge.closeSoftware()}) }
                    ActionButton { objectName: "lockStaffControls"; text: "Lock Staff Controls"; secondary: true; Layout.fillWidth: true; onClicked: { staffPassword.text = ""; root.cancelConfirmation(); bridge.lockStaffAccess() } }
                }
            }
        }
        Text { text: "Developed by Sumit Kumar · StickForYou"; color: "#7892a4"; font.pixelSize: 11; Layout.alignment: Qt.AlignHCenter }
    }
}
