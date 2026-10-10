import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    Theme { id: theme }
    readonly property int brandSize: Math.min(88, Math.max(64, height * 0.08))
    property string staffPanel: ""
    function validMinutes(value, allowZero) {
        let text = String(value).trim()
        if (!/^[0-9]+$/.test(text)) return false
        let minutes = Number(text)
        return Number.isInteger(minutes) && minutes <= 1440 && minutes >= (allowZero ? 0 : 1)
    }
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
    Rectangle { anchors.fill: parent; color: "#0B1120" }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 22; spacing: 9
        RowLayout { Layout.fillWidth: true; spacing: 10
            BrandLogo { Layout.preferredWidth: page.brandSize; Layout.preferredHeight: page.brandSize }
            Avatar { diameter: Math.round(page.brandSize * 0.7) }
            ColumnLayout { Layout.fillWidth: true
                Text { text: bridge.view.cafeName || "GameGrid"; color: "#F4F7FB"; font.pixelSize: 18; font.bold: true }
                Text { text: bridge.view.ownName || "User PC"; color: "#A8B8CA" }
            }
            Text { text: bridge.view.connectionNote || "LOCAL CAFE"; color: "#A8B8CA"; font.pixelSize: 12 }
        }
        Item { Layout.fillHeight: true }
        Panel {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Math.min(page.width - 44, 650)
            implicitHeight: consoleBody.implicitHeight + 36
            ColumnLayout {
                id: consoleBody; anchors.fill: parent; anchors.margins: 18; spacing: 10
                Text { text: bridge.view.accessAllowed ? "YOUR SESSION IS ACTIVE" : "THIS PC IS LOCKED"; color: bridge.view.accessAllowed ? "#A4B36A" : "#F2A65A"; font.bold: true; font.pixelSize: 13 }
                Text { text: bridge.view.player || "Guest"; color: "#F4F7FB"; font.pixelSize: 25; font.bold: true }
                Text { text: bridge.view.phase || "WAITING"; color: "#A8B8CA"; font.pixelSize: 14 }
                Text { objectName: "consoleSessionTimer"; text: bridge.view.timeText || "00:00:00"; color: bridge.view.phase === "BUFFER" ? theme.buffer : bridge.view.phase === "PAUSED" ? theme.paused : bridge.view.phase === "GRACE" || bridge.view.phase === "EXPIRED" ? theme.grace : theme.active; font.pixelSize: 42; font.bold: true; visible: bridge.view.hasSession }
                Text { text: bridge.view.feedback || "Ask staff to start or renew your session."; color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
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
        Panel {
            objectName: "staffAccessPanel"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Math.min(page.width - 44, 650)
            Layout.preferredHeight: Math.min(staffBody.implicitHeight + 24,
                                             Math.max(150, page.height * 0.34))
            Layout.minimumHeight: 150
            visible: page.staffPanel === "staff"
            ScrollView {
                objectName: "staffAccessScroll"
                anchors.fill: parent
                anchors.margins: 12
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    id: staffBody
                    objectName: "staffAccessContent"
                    width: parent.width
                    spacing: 8
                Text { text: bridge.staffAuthorized ? "Staff Controls" : "Staff Access · Admin password required"; color: "#F4F7FB"; font.bold: true }
                ColumnLayout {
                    objectName: "staffAuthenticationForm"
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
                    Text { objectName: "staffSessionTimer"; text: (bridge.view.phase || "WAITING") + " · " + (bridge.view.timeText || "00:00:00"); color: bridge.view.phase === "BUFFER" ? theme.buffer : bridge.view.phase === "PAUSED" ? theme.paused : bridge.view.phase === "GRACE" || bridge.view.phase === "EXPIRED" ? theme.grace : theme.active; font.bold: true }
                    RowLayout {
                        objectName: "staffSessionSetupRow"
                        visible: !bridge.view.hasSession
                        Layout.fillWidth: true
                        DarkComboBox { id: staffKind; objectName: "staffSessionKind"; model: ["Timed", "No timer"]; Layout.fillWidth: true; Layout.minimumWidth: 100; onActivated: bridge.touchStaffAccess() }
                        DarkComboBox { id: staffPaid; objectName: "staffSessionDuration"; model: ["15", "30", "60", "120", "Custom Minutes"]; currentIndex: 2; visible: staffKind.currentIndex === 0; Layout.fillWidth: true; Layout.minimumWidth: 120; onActivated: bridge.touchStaffAccess() }
                        TextField { id: staffCustomPaid; visible: staffKind.currentIndex === 0 && staffPaid.currentIndex === 4; placeholderText: "Minutes"; Layout.fillWidth: true; Layout.minimumWidth: 80; inputMethodHints: Qt.ImhDigitsOnly; onTextEdited: bridge.touchStaffAccess() }
                        TextField { id: staffBuffer; text: "0"; placeholderText: "Buffer"; Layout.fillWidth: true; Layout.minimumWidth: 80; inputMethodHints: Qt.ImhDigitsOnly; onTextEdited: bridge.touchStaffAccess() }
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
                            if ((kind === "timed" && !page.validMinutes(paid, false)) || !page.validMinutes(staffBuffer.text, true)) {
                                root.ask("Invalid session time", "Use whole minutes: paid time 1 to 1440 and buffer 0 to 1440.", "OK", function(){})
                                return
                            }
                            root.ask("Start local session", "Start this " + (kind === "timed" ? paid + " minute timed" : "no-timer") + " session with " + staffBuffer.text + " buffer minutes?", "Start Session", function(){ bridge.staffStartSession(kind, paid, staffBuffer.text) })
                        }
                    }
                    RowLayout {
                        visible: bridge.view.hasSession && bridge.view.kind === "timed"
                        Layout.fillWidth: true
                        DarkComboBox { id: staffAdd; objectName: "staffAddDuration"; model: ["1", "2", "5", "15", "30", "60", "Custom Minutes"]; currentIndex: 3; Layout.fillWidth: true; onActivated: bridge.touchStaffAccess() }
                        TextField { id: staffCustomAdd; visible: staffAdd.currentIndex === 6; placeholderText: "Minutes"; Layout.preferredWidth: 86; inputMethodHints: Qt.ImhDigitsOnly; onTextEdited: bridge.touchStaffAccess() }
                        ActionButton {
                            objectName: "staffAddButton"
                            text: "Add Time"
                            customFill: true
                            customColor: theme.active
                            enabled: !bridge.busy
                            onClicked: {
                                bridge.touchStaffAccess()
                                let raw = staffAdd.currentIndex === 6 ? staffCustomAdd.text : staffAdd.currentText
                                if (!page.validMinutes(raw, false)) {
                                    root.ask("Invalid time", "Use a whole number from 1 to 1440 minutes.", "OK", function(){})
                                    return
                                }
                                let minutes = Number(raw)
                                let preview = bridge.previewStaffAdd(minutes)
                                root.ask("Confirm added time", preview, "Add Time", function(){ bridge.staffAddTime(minutes) })
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#455C73" }
                    RowLayout { objectName: "staffActionRow"; Layout.fillWidth: true; spacing: 8
                        ActionButton {
                            objectName: "staffRoleAction"
                            text: bridge.view.adminNode ? "Sign Out" : "Switch to Admin"
                            secondary: true
                            Layout.fillWidth: true
                            enabled: bridge.view.canSwitchAdmin && !bridge.view.hasSession && !bridge.busy
                            onClicked: {
                                if (bridge.view.adminNode) root.confirmUserConsoleSignOut()
                                else bridge.switchAdmin()
                            }
                        }
                        ActionButton { objectName: "staffCloseSoftware"; text: "Close Software"; Layout.fillWidth: true; danger: true; enabled: !bridge.busy; onClicked: {
                            if (bridge.hasLocalSession()) {
                                root.ask("Active Session Detected",
                                         "An active gaming session is running on this computer. Closing GameGrid will end this session and save its history. Do you want to continue?",
                                         "End Session & Exit", function(){ bridge.closeSoftware() })
                            } else {
                                root.ask("Close software", "Close GameGrid on this PC and restore the normal Windows desktop?",
                                         "Close", function(){ bridge.closeSoftware() })
                            }
                        } }
                        ActionButton { objectName: "lockStaffControls"; text: "Lock Controls"; secondary: true; Layout.fillWidth: true; onClicked: { staffPassword.text = ""; root.cancelConfirmation(); bridge.lockStaffAccess() } }
                    }
                }
                }
            }
        }
        Text { text: "Developed by Sumit Kumar · StickForYou"; color: "#8398AC"; font.pixelSize: 11; Layout.alignment: Qt.AlignHCenter }
    }
}
