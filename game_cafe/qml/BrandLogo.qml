import QtQuick

Item {
    id: root
    implicitWidth: 190
    implicitHeight: 86

    Image {
        id: logo
        anchors.fill: parent
        source: bridge.view.appLogoSource || ""
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        sourceSize.width: 380
        sourceSize.height: 172
        visible: source.toString() !== "" && status === Image.Ready
    }
    Text {
        anchors.centerIn: parent
        text: "StickForYou"
        color: "#f5fbff"
        font.pixelSize: 28
        font.bold: true
        visible: !logo.visible
    }
}
