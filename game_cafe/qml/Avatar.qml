import QtQuick

Item {
    id: avatar
    property int diameter: 56
    width: diameter
    height: diameter
    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: "#274C58"
        border.color: "#64BCC1"
        border.width: 1
        Text {
            anchors.centerIn: parent
            text: (bridge.view.cafeName || "GC").substring(0, 2).toUpperCase()
            color: "#D8F5F4"
            font.pixelSize: avatar.diameter * 0.32
            font.weight: Font.Bold
        }
    }
    Image {
        anchors.fill: parent
        source: bridge.view.avatarSource || ""
        fillMode: Image.PreserveAspectCrop
        visible: bridge.view.hasAvatar && status === Image.Ready
    }
}
