import QtQuick

Item {
    id: root
    property string text: ""
    property color color: "#F4F7FB"
    property int pixelSize: 12
    property bool bold: false
    readonly property bool overflowing: label.implicitWidth > width + 1
    readonly property bool marqueeRunning: marquee.running
    implicitHeight: label.implicitHeight
    clip: true

    function resetMarquee() {
        marquee.stop()
        label.x = 0
        if (overflowing && visible)
            Qt.callLater(function() { if (root.overflowing && root.visible) marquee.start() })
    }

    onTextChanged: resetMarquee()
    onWidthChanged: resetMarquee()
    onVisibleChanged: resetMarquee()
    Component.onCompleted: resetMarquee()

    Text {
        id: label
        text: root.text
        color: root.color
        font.pixelSize: root.pixelSize
        font.bold: root.bold
        verticalAlignment: Text.AlignVCenter
    }

    SequentialAnimation {
        id: marquee
        loops: Animation.Infinite
        PauseAnimation { duration: 900 }
        NumberAnimation {
            target: label
            property: "x"
            to: Math.min(0, root.width - label.implicitWidth)
            duration: Math.max(700, (label.implicitWidth - root.width) * 22)
            easing.type: Easing.InOutSine
        }
        PauseAnimation { duration: 900 }
        NumberAnimation {
            target: label
            property: "x"
            to: 0
            duration: Math.max(700, (label.implicitWidth - root.width) * 22)
            easing.type: Easing.InOutSine
        }
        onStopped: label.x = 0
    }
}
