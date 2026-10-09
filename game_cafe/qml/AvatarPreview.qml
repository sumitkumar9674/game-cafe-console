import QtQuick

Item {
    id: root
    property url source
    property bool crop: false

    implicitWidth: 184
    implicitHeight: 184

    Rectangle {
        anchors.fill: parent
        radius: 14
        color: "#111B2B"
        border.color: "#455C73"
        clip: true
        Image {
            id: preview
            anchors.fill: parent
            anchors.margins: root.crop ? 0 : 8
            source: root.source
            fillMode: root.crop ? Image.PreserveAspectCrop : Image.PreserveAspectFit
            asynchronous: true
            sourceSize.width: 368
            sourceSize.height: 368
            visible: source.toString() !== "" && status === Image.Ready
        }
        Text {
            anchors.centerIn: parent
            width: parent.width - 28
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: root.source.toString() === "" ? "No valid image selected" : "Image unavailable"
            color: "#A8B8CA"
            visible: !preview.visible
        }
    }
}
