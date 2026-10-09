import QtQuick

Item {
    id: root
    implicitWidth: 190
    implicitHeight: 86

    Image {
        id: logo
        objectName: "gamegridLogoImage"
        anchors.fill: parent
        source: bridge.view.appLogoSource || ""
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        sourceSize.width: 256
        sourceSize.height: 256
        visible: source.toString() !== "" && status === Image.Ready
    }
    Text {
        anchors.centerIn: parent
        width: parent.width
        height: parent.height
        text: "GameGrid"
        color: "#F4F7FB"
        font.pixelSize: Math.min(28, root.height * 0.3)
        fontSizeMode: Text.Fit
        font.bold: true
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        visible: !logo.visible
    }
}
