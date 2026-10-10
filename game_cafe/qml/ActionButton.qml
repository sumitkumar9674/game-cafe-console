import QtQuick
import QtQuick.Controls

Button {
    id: button
    Theme { id: theme }
    property bool secondary: false
    property bool danger: false
    property bool customFill: false
    property color customColor: theme.active
    implicitHeight: 42
    implicitWidth: Math.max(105, label.implicitWidth + 28)
    enabled: !bridge.busy
    background: Rectangle {
        radius: theme.smallRadius
        color: !button.enabled ? "#273449" : button.customFill ?
               (button.down ? Qt.darker(button.customColor, 1.2) : button.hovered ? Qt.lighter(button.customColor, 1.12) : button.customColor) : button.danger ?
               (button.down ? "#91354E" : button.hovered ? "#CA5873" : theme.danger) :
               button.secondary ? (button.down ? "#304A61" : button.hovered ? "#2B4158" : "#203348") :
               button.down ? "#B83F74" : button.hovered ? theme.accentHover : theme.accent
        border.color: button.activeFocus ? theme.active :
                      button.secondary ? "#455C73" : "transparent"
        Behavior on color { ColorAnimation { duration: 130 } }
    }
    contentItem: Text {
        id: label
        text: button.text
        color: button.enabled ? theme.text : "#8797AA"
        font.pixelSize: 14
        font.weight: Font.DemiBold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
