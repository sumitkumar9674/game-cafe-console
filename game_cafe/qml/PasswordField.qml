import QtQuick
import QtQuick.Controls

Item {
    id: root
    property alias text: input.text
    property alias placeholderText: input.placeholderText
    readonly property alias displayText: input.displayText
    property bool passwordVisible: false
    signal accepted()

    implicitWidth: input.implicitWidth
    implicitHeight: input.implicitHeight

    TextField {
        id: input
        anchors.fill: parent
        rightPadding: reveal.width + 12
        echoMode: root.passwordVisible ? TextInput.Normal : TextInput.Password
        onAccepted: root.accepted()
    }
    ToolButton {
        id: reveal
        objectName: "passwordRevealButton"
        width: 40
        height: parent.height
        anchors.right: parent.right
        text: "👁"
        opacity: root.passwordVisible ? 1.0 : 0.72
        focusPolicy: Qt.StrongFocus
        Accessible.name: root.passwordVisible ? "Hide password" : "Show password"
        ToolTip.visible: hovered
        ToolTip.text: Accessible.name
        onClicked: root.passwordVisible = !root.passwordVisible
    }
}
