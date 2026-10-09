pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    implicitHeight: 40
    leftPadding: 12
    rightPadding: 28
    contentItem: Text {
        text: control.displayText
        color: "#F4F7FB"
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Text {
        text: "▾"
        color: "#64BCC1"
        anchors.right: parent.right
        anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
    }
    background: Rectangle {
        radius: 6
        color: !control.enabled ? "#273449" : control.hovered ? "#2C455D" : "#203348"
        border.color: control.activeFocus || control.popup.visible ? "#D85288" : "#455C73"
    }
    delegate: ItemDelegate {
        id: delegateItem
        required property var modelData
        required property int index
        width: control.popup.width - 8
        height: 38
        text: modelData && modelData.name !== undefined ? modelData.name : String(modelData)
        highlighted: control.highlightedIndex === index
        background: Rectangle {
            color: delegateItem.highlighted ? "#34546D" : delegateItem.hovered ? "#2C455D" : "#1B2B40"
        }
        contentItem: Text {
            text: delegateItem.text
            color: "#F4F7FB"
            verticalAlignment: Text.AlignVCenter
            leftPadding: 9
        }
    }
    popup: Popup {
        objectName: control.objectName + "Popup"
        y: control.height
        width: control.width
        implicitHeight: Math.min(250, contentItem.implicitHeight + 8)
        padding: 4
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        contentItem: ListView {
            objectName: control.objectName + "PopupList"
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator { }
        }
        background: Rectangle {
            objectName: control.objectName + "PopupBackground"
            color: "#1B2B40"
            border.color: "#D85288"
            radius: 6
        }
    }
}
