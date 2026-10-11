import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: root
    visible: true
    width: 1000
    height: 680
    minimumWidth: 720
    minimumHeight: 560
    title: "GameGrid"
    color: "#0B1120"
    Material.theme: Material.Dark
    Material.accent: "#D85288"
    property var confirmation: ({})
    property string confirmationInput: ""
    property var notice: bridge.notice
    function ask(title, message, label, action, cancelLabel, cancelAction,
                 requiredText, showCloseControl) {
        confirmationInput = ""
        confirmation = ({ title: title, message: message, label: label,
                          action: action, cancelLabel: cancelLabel || "Cancel",
                          cancelAction: cancelAction,
                          requiredText: requiredText || "",
                          showCloseControl: !!showCloseControl })
    }
    function confirmAdminExit() {
        ask("Exit GameGrid",
            bridge.adminExitMessage(),
            "Exit Application", function() { bridge.closeAdmin() })
    }
    function confirmAdminSignOut() {
        ask("Sign out of Admin?",
            "You will return to the GameGrid login screen. The Admin system will continue running and connected PCs will remain managed.",
            "Continue", function() {
                root.ask("Confirm Sign Out",
                         "Are you sure you want to sign out of the Admin dashboard?",
                         "Confirm Sign Out", function() { bridge.signOutAdmin() },
                         "Go Back", function() { root.confirmAdminSignOut() })
            })
    }
    function confirmUserConsoleSignOut() {
        ask("Sign out of User Console?",
            "You will return to the GameGrid login screen. The Admin system will continue running in the background.",
            "Continue", function() {
                root.ask("Confirm Sign Out",
                         "Are you sure you want to sign out of the User Console?",
                         "Confirm Sign Out", function() { bridge.switchAdmin() },
                         "Go Back", function() { root.confirmUserConsoleSignOut() })
            })
    }
    function cancelConfirmation() {
        if (confirmation.cancelAction) {
            confirmation.cancelAction()
            return
        }
        confirmation = ({})
        confirmationInput = ""
    }
    function acceptConfirmation() {
        if (confirmation.requiredText &&
                confirmationInput !== confirmation.requiredText) return
        let action = confirmation.action
        let input = confirmationInput
        confirmation = ({})
        confirmationInput = ""
        if (action) action(input)
    }
    onClosing: function(close) {
        close.accepted = false
        if (bridge.childMode) return
        if (bridge.mode === "admin") {
            if (root.notice.title) bridge.clearNotice()
            confirmAdminExit()
        } else if (bridge.mode === "widget") {
            if (root.confirmation.title) {
                root.cancelConfirmation()
                return
            }
            root.showMinimized()
        } else {
            bridge.closeApplication()
        }
    }
    Loader {
        objectName: "mainLoader"
        anchors.fill: parent
        source: bridge.mode === "admin" ? "Admin.qml" :
                bridge.mode === "console" ? "Console.qml" :
                bridge.mode === "widget" ? "Widget.qml" :
                bridge.mode === "splash" ? "Splash.qml" : "Onboarding.qml"
    }
    FocusScope {
        id: modalScope
        anchors.fill: parent
        visible: !!root.confirmation.title || !!root.notice.title
        z: 20
        focus: visible
        onVisibleChanged: if (visible) modalScope.forceActiveFocus()
        Keys.onEscapePressed: {
            if (root.notice.title) bridge.clearNotice()
            else root.cancelConfirmation()
        }
        Rectangle { anchors.fill: parent; color: "#BF080D18" }
        MouseArea { anchors.fill: parent }
        Panel {
            width: Math.min(parent.width - 40, 490)
            height: modalColumn.implicitHeight + 48
            anchors.centerIn: parent
            border.color: root.notice.error ? "#D16C83" : "#64BCC1"
            ColumnLayout {
                id: modalColumn
                anchors.fill: parent
                anchors.margins: 24
                spacing: 18
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: root.notice.title || root.confirmation.title || ""; color: "#F4F7FB"; font.pixelSize: 22; font.bold: true; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    ActionButton {
                        objectName: "confirmationCloseButton"
                        text: "×"
                        secondary: true
                        Layout.preferredWidth: 42
                        Accessible.name: "Close confirmation"
                        visible: !root.notice.title
                                 && !!root.confirmation.showCloseControl
                        onClicked: root.cancelConfirmation()
                    }
                }
                Text { text: root.notice.message || root.confirmation.message || ""; color: "#A8B8CA"; font.pixelSize: 14; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                TextField {
                    objectName: "confirmationExactNameInput"
                    visible: !root.notice.title && !!root.confirmation.requiredText
                    Layout.fillWidth: true
                    placeholderText: "Enter the exact customer name"
                    text: root.confirmationInput
                    onTextChanged: root.confirmationInput = text
                }
                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    spacing: 10
                    ActionButton { objectName: "confirmationCancelButton"; text: root.confirmation.cancelLabel || "Cancel"; secondary: true; visible: !root.notice.title; onClicked: root.cancelConfirmation() }
                    ActionButton {
                        objectName: "confirmationAcceptButton"
                        text: root.notice.title ? "OK" : root.confirmation.label || "Confirm"
                        danger: !!root.notice.error || root.confirmation.label === "Exit Application"
                                || root.confirmation.label === "Delete User"
                                || root.confirmation.label === "End Session"
                        enabled: !root.confirmation.requiredText
                                 || root.confirmationInput === root.confirmation.requiredText
                        focus: modalScope.visible
                        onClicked: {
                            if (root.notice.title) bridge.clearNotice()
                            else root.acceptConfirmation()
                        }
                    }
                }
            }
        }
    }
}
