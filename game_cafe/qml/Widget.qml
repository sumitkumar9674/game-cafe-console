import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    Theme { id: theme }
    property string stopCustomerId: ""
    property string stopRetainerSessionId: ""
    function stopCooldownMessage() {
        let customer = bridge.view.registeredCustomer || ({})
        if (!customer.retainerCooldownKnown) {
            return "Your remaining retainer time will be saved, and your gaming desktop will be locked."
                 + "\n\nRestart Cooldown: Unavailable"
                 + "\n\nThe current cooldown duration is unavailable while the Admin connection is offline. You can still end this session safely."
        }
        let minutes = Number(customer.retainerCooldownMinutes || 0)
        if (minutes === 0) {
            return "Your remaining retainer time will be saved, and your gaming desktop will be locked."
                 + "\n\nNo Restart Cooldown"
                 + "\n\nYou can start another retainer session immediately after ending this one."
        }
        let duration = minutes + (minutes === 1 ? " minute" : " minutes")
        return "Your remaining retainer time will be saved, and your gaming desktop will be locked."
             + "\n\nRestart Cooldown: " + duration
             + "\n\nYou will need to wait " + duration + " before starting another retainer session."
    }
    function confirmRetainerStop() {
        if (root.confirmation.title || root.notice.title || bridge.busy
                || !bridge.view.retainerActive) return
        let customer = bridge.view.registeredCustomer || ({})
        if (!customer.customerId || !customer.retainerSessionId) return
        stopCustomerId = customer.customerId
        stopRetainerSessionId = customer.retainerSessionId
        let customerId = stopCustomerId
        let sessionId = stopRetainerSessionId
        root.ask("End Retainer Session?", stopCooldownMessage(),
                 "End Session", function() {
                     bridge.stopRetainerConfirmed(customerId, sessionId)
                 }, "Continue Playing", undefined, "", true)
    }
    Connections {
        target: root
        function onConfirmationChanged() {
            if (!root.confirmation.title) {
                page.stopCustomerId = ""
                page.stopRetainerSessionId = ""
            }
        }
    }
    Connections {
        target: bridge
        function onViewChanged() {
            if (!page.stopRetainerSessionId) return
            let customer = bridge.view.registeredCustomer || ({})
            if (!bridge.view.retainerActive
                    || customer.customerId !== page.stopCustomerId
                    || customer.retainerSessionId !== page.stopRetainerSessionId) {
                if (root.confirmation.title === "End Retainer Session?")
                    root.cancelConfirmation()
                page.stopCustomerId = ""
                page.stopRetainerSessionId = ""
            }
        }
    }
    implicitWidth: 480
    implicitHeight: Math.max(400, widgetContent.implicitHeight + 60)
    onImplicitWidthChanged: bridge.updateWidgetSize(Math.ceil(implicitWidth), Math.ceil(implicitHeight))
    onImplicitHeightChanged: bridge.updateWidgetSize(Math.ceil(implicitWidth), Math.ceil(implicitHeight))
    Panel {
        anchors.fill: parent
        anchors.margins: 12
        ScrollView {
            id: widgetScroll
            anchors.fill: parent
            anchors.margins: 18
            contentWidth: availableWidth
            contentHeight: widgetContent.implicitHeight
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: widgetContent
                width: widgetScroll.availableWidth
                height: implicitHeight
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    BrandLogo { Layout.preferredWidth: 64; Layout.preferredHeight: 64 }
                    Avatar { diameter: 42 }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text { text: bridge.view.cafeName || "GameGrid"; color: "#F4F7FB"; font.bold: true; font.pixelSize: 16; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                        Text { text: bridge.view.ownName || "User PC"; color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                    }
                }
                Text { objectName: "widgetSessionTimer"; text: bridge.view.retainerActive ? "RETAINER · " + bridge.view.retainerBalanceText : (bridge.view.phase || "WAITING") + " · " + (bridge.view.timeText || "00:00:00"); color: bridge.view.retainerActive ? theme.accent : bridge.view.phase === "BUFFER" ? theme.buffer : bridge.view.phase === "PAUSED" ? theme.paused : bridge.view.phase === "GRACE" || bridge.view.phase === "EXPIRED" ? theme.grace : theme.active; font.pixelSize: 19; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                Text { text: "Player: " + (bridge.view.player || "Guest"); color: "#F4F7FB"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
                Text { id: connectionMessage; objectName: "widgetConnectionMessage"; text: bridge.view.connectionNote || "Local cafe connection"; color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: connectionMessage.implicitHeight; Layout.preferredHeight: connectionMessage.implicitHeight }
                ActionButton { objectName: "widgetStopRetainerButton"; text: "Stop Retainer"; danger: true; visible: bridge.view.retainerActive; Layout.fillWidth: true; onClicked: page.confirmRetainerStop() }
                ActionButton { objectName: "widgetOpenConsoleButton"; text: "Open User Console"; visible: !bridge.view.retainerActive; Layout.fillWidth: true; onClicked: bridge.openConsole() }
                Text { objectName: "widgetFooter"; text: "Developed by Sumit Kumar · StickForYou"; color: "#8398AC"; font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.minimumHeight: implicitHeight }
            }
        }
    }
}
