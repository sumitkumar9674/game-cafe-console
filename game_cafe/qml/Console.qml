import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    Theme { id: theme }
    readonly property int brandSize: Math.min(88, Math.max(64, height * 0.08))
    property string staffPanel: ""
    property string accountPanel: ""
    property string selectedAccountId: ""
    property string selectedAccountName: ""
    property int selectedLoginCooldownExpiresAt: 0
    property int currentUtc: Math.floor(Date.now() / 1000)
    readonly property int selectedLoginCooldownRemaining: Math.max(
        0, selectedLoginCooldownExpiresAt - currentUtc)
    readonly property int retainerCooldownRemaining: Math.max(
        0, (bridge.view.registeredCustomer
            ? bridge.view.registeredCustomer.retainerCooldownExpiresAt || 0 : 0)
           - currentUtc)
    function cooldownText(seconds) {
        let hours = Math.floor(seconds / 3600)
        let minutes = Math.floor((seconds % 3600) / 60)
        let remainder = seconds % 60
        function two(value) { return value < 10 ? "0" + value : String(value) }
        return hours > 0 ? two(hours) + ":" + two(minutes) + ":" + two(remainder)
                         : two(minutes) + ":" + two(remainder)
    }
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
    function confirmRetainerStart() {
        root.ask("Start Retainer Session?",
                 "Customer: " + bridge.view.registeredCustomer.name
                 + "\nRemaining balance: " + bridge.view.retainerBalanceText
                 + "\n\nTime is deducted while the gaming desktop is unlocked.",
                 "Continue", function() {
            root.ask("Confirm Retainer Play",
                     "The desktop will unlock and retainer time will begin immediately. Stop Retainer to lock and preserve remaining time.",
                     "Start Playing", function(){ bridge.startRetainer() },
                     "Go Back", function(){ page.confirmRetainerStart() })
        })
    }
    Component.onDestruction: bridge.lockStaffAccess()
    Timer {
        interval: 1000; running: true; repeat: true
        onTriggered: page.currentUtc = Math.floor(Date.now() / 1000)
    }
    Connections {
        target: bridge
        function onStaffAuthChanged() {
            if (bridge.staffAuthorized) staffPassword.text = ""
            else root.cancelConfirmation()
        }
        function onViewChanged() {
            if (!bridge.view.guestMode && accountPanel === "password") {
                accountPassword.text = ""
                accountPanel = ""
            }
            if ((bridge.view.feedback || "").startsWith("Password changed successfully")) {
                currentPassword.text = ""
                newPassword.text = ""
                confirmPassword.text = ""
                accountPanel = ""
            }
        }
    }
    Rectangle { anchors.fill: parent; color: "#0B1120" }
    Image {
        objectName: "lockedConsoleWallpaper"
        anchors.fill: parent
        source: bridge.view.wallpaperSource || ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: false
        visible: source.toString() !== "" && status === Image.Ready
    }
    Popup {
        id: accountPopup
        objectName: "customerAccountPopup"
        visible: page.accountPanel !== ""
        modal: true
        closePolicy: Popup.NoAutoClose
        width: Math.min(520, page.width - 48)
        height: Math.min(440, page.height - 70)
        x: (page.width - width) / 2
        y: (page.height - height) / 2
        background: Panel { border.color: theme.accent }
        contentItem: ColumnLayout {
            spacing: 12
            RowLayout { Layout.fillWidth: true
                Text {
                    text: page.accountPanel === "selector" ? "Select Account" :
                          page.accountPanel === "password" ? page.selectedAccountName :
                          "Change Password"
                    color: "#F4F7FB"; font.pixelSize: 21; font.bold: true
                    Layout.fillWidth: true
                }
                ActionButton { text: "Close"; secondary: true; onClicked: {
                    accountPassword.text = ""; currentPassword.text = ""
                    newPassword.text = ""; confirmPassword.text = ""
                    page.accountPanel = ""
                } }
            }
            ColumnLayout {
                visible: page.accountPanel === "selector"
                Layout.fillWidth: true; Layout.fillHeight: true
                TextField {
                    objectName: "customerAccountSearch"
                    placeholderText: "Search customers"
                    Layout.fillWidth: true
                    onTextChanged: bridge.setCustomerSearch(text)
                }
                Text {
                    text: bridge.view.registeredAdminOnline ?
                          "Choose your registered account." :
                          "Admin Offline · Registered login is unavailable."
                    color: bridge.view.registeredAdminOnline ? "#A8B8CA" : theme.grace
                }
                ListView {
                    id: accountList
                    objectName: "customerAccountList"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    model: bridge.customerModel; clip: true; spacing: 6
                    delegate: Rectangle {
                        required property var rowData
                        width: accountList.width; height: 52; radius: 8
                        color: "#203348"; border.color: "#35475D"
                        Text { anchors.verticalCenter: parent.verticalCenter; anchors.left: parent.left; anchors.leftMargin: 12; text: rowData.name; color: "#F4F7FB"; font.bold: true }
                        MouseArea { anchors.fill: parent; enabled: bridge.view.registeredLoginAvailable; onClicked: {
                            page.selectedAccountId = parent.rowData.customerId
                            page.selectedAccountName = parent.rowData.name
                            page.selectedLoginCooldownExpiresAt = parent.rowData.loginCooldownExpiresAt || 0
                            accountPassword.text = ""
                            page.accountPanel = "password"
                        } }
                    }
                    Text { anchors.centerIn: parent; text: "No matching customers"; color: "#8398AC"; visible: accountList.count === 0 }
                }
            }
            ColumnLayout {
                visible: page.accountPanel === "password"
                Layout.fillWidth: true
                Text { text: "Enter the password for " + page.selectedAccountName + "."; color: "#A8B8CA" }
                Text {
                    objectName: "customerLoginCooldownMessage"
                    text: "Login Cooldown Active Â· " + page.cooldownText(page.selectedLoginCooldownRemaining)
                    color: theme.grace; font.bold: true
                    visible: page.selectedLoginCooldownRemaining > 0
                }
                PasswordField { id: accountPassword; objectName: "customerLoginPassword"; placeholderText: "Customer password"; Layout.fillWidth: true; enabled: page.selectedLoginCooldownRemaining === 0; onAccepted: { if (enabled) bridge.loginCustomer(page.selectedAccountId, text) } }
                Text { text: bridge.view.registeredAdminOnline ? "" : "Admin Offline · Try again when GameGrid reconnects."; color: theme.grace; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                RowLayout { Layout.alignment: Qt.AlignRight
                    ActionButton { text: "Back"; secondary: true; onClicked: { accountPassword.text = ""; page.accountPanel = "selector" } }
                    ActionButton { objectName: "customerLoginButton"; text: "Log In"; enabled: bridge.view.registeredAdminOnline && page.selectedLoginCooldownRemaining === 0 && accountPassword.text.length > 0 && !bridge.busy; onClicked: bridge.loginCustomer(page.selectedAccountId, accountPassword.text) }
                }
            }
            ColumnLayout {
                visible: page.accountPanel === "change"
                Layout.fillWidth: true
                PasswordField { id: currentPassword; objectName: "customerCurrentPassword"; placeholderText: "Current password"; Layout.fillWidth: true }
                PasswordField { id: newPassword; objectName: "customerNewPassword"; placeholderText: "New password (5–128 characters)"; Layout.fillWidth: true }
                PasswordField { id: confirmPassword; objectName: "customerConfirmPassword"; placeholderText: "Confirm new password"; Layout.fillWidth: true }
                ActionButton { objectName: "customerChangePasswordSubmit"; text: "Save Password"; enabled: currentPassword.text.length > 0 && newPassword.text.length >= 5 && newPassword.text === confirmPassword.text && bridge.view.registeredAdminOnline && !bridge.busy; onClicked: bridge.changeCustomerPassword(currentPassword.text, newPassword.text, confirmPassword.text) }
            }
            Text { text: bridge.view.feedback || ""; color: (bridge.view.feedback || "").indexOf("failed") >= 0 ? theme.danger : "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        }
    }
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
        Panel {
            objectName: "customerAccountBar"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Math.min(page.width - 44, 760)
            implicitHeight: 58
            RowLayout { anchors.fill: parent; anchors.margins: 10; spacing: 10
                ColumnLayout { Layout.fillWidth: true; spacing: 2
                    Text { text: bridge.view.guestMode ? "Guest" : bridge.view.registeredCustomer.name; color: "#F4F7FB"; font.pixelSize: 17; font.bold: true }
                    Text { text: bridge.view.guestMode ? "Local guest access" : "REGISTERED CUSTOMER · " + (bridge.view.retainerFinalizing ? "Finalizing balance…" : bridge.view.retainerBalanceText); color: bridge.view.guestMode ? "#8398AC" : theme.accent; font.pixelSize: 10; font.bold: true }
                }
                ActionButton { objectName: "selectCustomerAccountButton"; text: "Select Account"; visible: bridge.view.guestMode; enabled: !bridge.view.hasOrdinarySession; onClicked: page.accountPanel = "selector" }
                ActionButton { objectName: "changeCustomerPasswordButton"; text: "Change Password"; secondary: true; visible: !bridge.view.guestMode; enabled: bridge.view.registeredAdminOnline && !bridge.view.retainerActive && !bridge.view.hasOrdinarySession; onClicked: page.accountPanel = "change" }
                ActionButton { objectName: "logoutCustomerButton"; text: "Log Out"; secondary: true; visible: !bridge.view.guestMode; enabled: !bridge.view.retainerActive && !bridge.view.hasOrdinarySession && bridge.view.registeredAdminOnline; onClicked: bridge.logoutCustomer() }
            }
        }
        Item { Layout.fillHeight: true }
        Panel {
            objectName: "gamingControlsCard"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Math.min(page.width - 44, 760)
            implicitHeight: consoleBody.implicitHeight + 36
            ColumnLayout {
                id: consoleBody; anchors.fill: parent; anchors.margins: 18; spacing: 10
                Text { text: bridge.view.accessAllowed ? "YOUR SESSION IS ACTIVE" : "THIS PC IS LOCKED"; color: bridge.view.accessAllowed ? "#A4B36A" : "#F2A65A"; font.bold: true; font.pixelSize: 13 }
                RowLayout { Layout.fillWidth: true; spacing: 14
                    ColumnLayout { Layout.fillWidth: true; spacing: 7
                        Text { text: "ORDINARY SESSION"; color: "#BED1DA"; font.bold: true; font.pixelSize: 11 }
                        Text { text: bridge.view.phase || "WAITING"; color: "#A8B8CA"; font.pixelSize: 13 }
                        Text { objectName: "consoleSessionTimer"; text: bridge.view.timeText || "00:00:00"; color: bridge.view.phase === "BUFFER" ? theme.buffer : bridge.view.phase === "PAUSED" ? theme.paused : bridge.view.phase === "GRACE" || bridge.view.phase === "EXPIRED" ? theme.grace : theme.active; font.pixelSize: 34; font.bold: true; visible: bridge.view.hasOrdinarySession }
                        Text { text: bridge.view.hasOrdinarySession ? "Managed by staff · Retainer balance is not used" : "No ordinary session is active."; color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        RowLayout { Layout.fillWidth: true; spacing: 8
                            ActionButton { text: "Open desktop"; visible: bridge.view.hasOrdinarySession && bridge.view.accessAllowed && !bridge.view.retainerActive; onClicked: bridge.goDesktop() }
                            ActionButton { text: bridge.view.requestCooldown > 0 ? "Wait " + bridge.view.requestCooldown + "s" : "Request unlock"; visible: !bridge.view.hasOrdinarySession && !bridge.view.retainerActive; enabled: bridge.view.requestCooldown <= 0 && !bridge.busy; onClicked: bridge.requestUnlock() }
                        }
                        ActionButton { text: "End ordinary session"; danger: true; visible: bridge.view.hasOrdinarySession; enabled: !bridge.busy; onClicked: root.ask("End session", "End your current ordinary session and lock this PC?", "End session", function(){bridge.customerEnd()}) }
                    }
                    Rectangle { visible: !bridge.view.guestMode; Layout.preferredWidth: 1; Layout.fillHeight: true; color: "#455C73" }
                    ColumnLayout { visible: !bridge.view.guestMode; Layout.fillWidth: true; spacing: 7
                        Text { text: "RETAINER"; color: theme.accent; font.bold: true; font.pixelSize: 11 }
                        Text { objectName: "consoleRetainerTimer"; text: bridge.view.retainerFinalizing ? "Finalizing…" : (bridge.view.retainerBalanceText || "00:00:00"); color: theme.accent; font.pixelSize: bridge.view.retainerFinalizing ? 20 : 34; font.bold: true }
                        Text { text: bridge.view.registeredAdminOnline ? "Admin Online" : "Admin Offline · Retainer play unavailable"; color: bridge.view.registeredAdminOnline ? theme.success : theme.grace; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Text {
                            objectName: "retainerRestartCooldownMessage"
                            text: "Retainer Restart Cooldown Active · "
                                  + page.cooldownText(page.retainerCooldownRemaining)
                            color: theme.grace; font.bold: true
                            visible: page.retainerCooldownRemaining > 0
                            wrapMode: Text.WordWrap; Layout.fillWidth: true
                        }
                        ActionButton {
                            objectName: "playUsingRetainerButton"
                            text: "Play Using Retainer"
                            visible: !bridge.view.retainerActive
                            enabled: bridge.view.registeredAdminOnline && page.retainerCooldownRemaining === 0 && bridge.view.retainerBalanceSeconds > 0 && !bridge.view.hasOrdinarySession && !bridge.busy
                            onClicked: page.confirmRetainerStart()
                        }
                        ActionButton { objectName: "stopRetainerConsoleButton"; text: "Stop Retainer"; danger: true; visible: bridge.view.retainerActive; onClicked: bridge.stopRetainer() }
                    }
                }
                Text { text: bridge.view.feedback || "Ask staff to start or renew an ordinary session."; color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
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
                        visible: !bridge.view.hasSession && !bridge.view.retainerActive
                        Layout.fillWidth: true
                        DarkComboBox { id: staffKind; objectName: "staffSessionKind"; model: ["Timed", "No timer"]; Layout.fillWidth: true; Layout.minimumWidth: 100; onActivated: bridge.touchStaffAccess() }
                        DarkComboBox { id: staffPaid; objectName: "staffSessionDuration"; model: ["15", "30", "60", "120", "Custom Minutes"]; currentIndex: 2; visible: staffKind.currentIndex === 0; Layout.fillWidth: true; Layout.minimumWidth: 120; onActivated: bridge.touchStaffAccess() }
                        TextField { id: staffCustomPaid; visible: staffKind.currentIndex === 0 && staffPaid.currentIndex === 4; placeholderText: "Minutes"; Layout.fillWidth: true; Layout.minimumWidth: 80; inputMethodHints: Qt.ImhDigitsOnly; onTextEdited: bridge.touchStaffAccess() }
                        TextField { id: staffBuffer; text: "0"; placeholderText: "Buffer"; Layout.fillWidth: true; Layout.minimumWidth: 80; inputMethodHints: Qt.ImhDigitsOnly; onTextEdited: bridge.touchStaffAccess() }
                    }
                    ActionButton {
                        objectName: "staffStartButton"
                        text: "Start / Set Session"
                        visible: !bridge.view.hasSession && !bridge.view.retainerActive
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
