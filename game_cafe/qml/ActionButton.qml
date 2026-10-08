import QtQuick
import QtQuick.Controls

Button {
    id: button
    Theme { id: theme }
    property bool secondary: false
    property bool danger: false
    implicitHeight: 42
    implicitWidth: Math.max(105, label.implicitWidth + 28)
    enabled: !bridge.busy
    background: Rectangle {
        radius: theme.smallRadius
        color: !button.enabled ? "#283548" : button.danger ? theme.danger :
               button.secondary ? (button.hovered ? "#30475c" : "#26384b") :
               button.down ? "#2aa5a8" : button.hovered ? theme.accentHover : theme.accent
        border.color: button.secondary ? "#415368" : "transparent"
        Behavior on color { ColorAnimation { duration: 130 } }
    }
    contentItem: Text {
        id: label
        text: button.text
        color: button.enabled ? (button.secondary || button.danger ? "#f4f8fa" : "#10202d") : "#8997a5"
        font.pixelSize: 14
        font.weight: Font.DemiBold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
