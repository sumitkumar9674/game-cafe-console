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
    title: "Game Cafe Console"
    color: "#101c2b"
    Material.theme: Material.Dark
    Material.accent: "#35c7c7"
    property var confirmation: ({})
    property var notice: bridge.notice
    function ask(title, message, label, action) {
        confirmation = ({ title: title, message: message, label: label, action: action })
    }
    function confirmAdminExit() {
        ask("Exit Game Cafe Console",
            "Exiting disconnects this Admin PC. Existing sessions on other User PCs will continue independently.",
            "Exit Application", function() { bridge.closeAdmin() })
    }
    function cancelConfirmation() {
        confirmation = ({})
    }
    function acceptConfirmation() {
        let action = confirmation.action
        confirmation = ({})
        if (action) action()
    }
    onClosing: function(close) {
        close.accepted = false
        if (bridge.childMode) return
        if (bridge.mode === "admin") {
            if (root.notice.title) bridge.clearNotice()
            confirmAdminExit()
        } else if (bridge.mode === "widget") {
            bridge.hideWidget()
        } else if (bridge.mode === "compact") {
            bridge.expandWidget()
        } else {
            bridge.closeApplication()
        }
    }
    Loader {
        objectName: "mainLoader"
        anchors.fill: parent
        source: bridge.mode === "admin" ? "Admin.qml" :
                bridge.mode === "console" ? "Console.qml" :
                bridge.mode === "compact" ? "CompactTimer.qml" :
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
        Rectangle { anchors.fill: parent; color: "#ba07111d" }
        MouseArea { anchors.fill: parent }
        Panel {
            width: Math.min(parent.width - 40, 490)
            height: modalColumn.implicitHeight + 48
            anchors.centerIn: parent
            border.color: root.notice.error ? "#c56a7a" : "#35c7c7"
            ColumnLayout {
                id: modalColumn
                anchors.fill: parent
                anchors.margins: 24
                spacing: 18
                Text { text: root.notice.title || root.confirmation.title || ""; color: "#f6fbff"; font.pixelSize: 22; font.bold: true; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Text { text: root.notice.message || root.confirmation.message || ""; color: "#b9c9d6"; font.pixelSize: 14; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    spacing: 10
                    ActionButton { text: "Cancel"; secondary: true; visible: !root.notice.title; onClicked: root.cancelConfirmation() }
                    ActionButton {
                        text: root.notice.title ? "OK" : root.confirmation.label || "Confirm"
                        danger: !!root.notice.error || root.confirmation.label === "Exit Application"
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
