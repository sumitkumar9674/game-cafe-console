import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    objectName: "adminPage"
    Theme { id: theme }
    property string section: "Dashboard"
    property string startKind: "timed"
    property string addValue: "15"
    property string historyTargetId: ""
    property string historyPendingId: ""
    property int currentUtc: Math.floor(Date.now() / 1000)
    function cooldownText(seconds) {
        let hours = Math.floor(seconds / 3600)
        let minutes = Math.floor((seconds % 3600) / 60)
        let remainder = seconds % 60
        function two(value) { return value < 10 ? "0" + value : String(value) }
        return hours > 0 ? two(hours) + ":" + two(minutes) + ":" + two(remainder)
                         : two(minutes) + ":" + two(remainder)
    }
    Timer {
        interval: 1000; running: true; repeat: true
        onTriggered: page.currentUtc = Math.floor(Date.now() / 1000)
    }
    Timer {
        objectName: "notificationTimestampTimer"
        interval: 60000
        running: page.visible && (page.section === "Dashboard" || page.section === "Computers")
        repeat: true
        onTriggered: bridge.refresh()
    }
    function reviewRetainerAdjustment(preview) {
        if (!preview || !preview.operationId) return
        let verb = preview.action === "add" ? "Add" : "Reduce"
        let message = "Customer: " + preview.name
                    + "\nAction: " + verb
                    + "\nDuration: " + preview.durationText
                    + "\nPrevious balance: " + preview.previousText
                    + "\nNew balance: " + preview.resultingText
        root.ask(verb + " Retainer Time", message, "Continue", function() {
            root.ask("Confirm " + verb + " Time",
                     "Approve the final " + verb.toLowerCase()
                     + " of " + preview.durationText + " for " + preview.name + "?",
                     "Confirm " + verb, function() {
                         bridge.commitCustomerAdjustment(preview)
                     }, "Go Back", function() {
                         page.reviewRetainerAdjustment(preview)
                     })
        })
    }
    function confirmCustomerDeletion(customer) {
        root.ask("Delete User",
                 "Customer: " + customer.name
                 + "\nCustomer ID: " + customer.customerId
                 + "\nRemaining balance: " + customer.balanceText
                 + "\n\nThis permanently deletes the account and its retainer adjustment history.",
                 "Continue", function() {
            root.ask("Confirm Permanent Deletion",
                     "This action is irreversible. Enter the exact customer name to delete this account.",
                     "Delete User", function(enteredName) {
                         bridge.deleteCustomer(customer.customerId, enteredName,
                                               customer.revision)
                     }, "Go Back", function() {
                         page.confirmCustomerDeletion(customer)
                     }, customer.name)
        })
    }
    function validMinutes(value, allowZero) {
        let text = String(value).trim()
        if (!/^[0-9]+$/.test(text)) return false
        let minutes = Number(text)
        return Number.isInteger(minutes) && minutes <= 1440 && minutes >= (allowZero ? 0 : 1)
    }
    function historyTarget() {
        let targets = bridge.view.historyTargets || []
        for (let i = 0; i < targets.length; ++i)
            if (targets[i].pcId === historyTargetId) return targets[i]
        return null
    }
    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.preferredWidth: 220
            Layout.fillHeight: true
            color: "#111B2B"
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 18; spacing: 16
                RowLayout { Layout.fillWidth: true
                    Avatar { objectName: "adminCafeAvatar"; diameter: 78 }
                }
                Text { objectName: "adminCafeName"; text: bridge.view.cafeName || "Cafe"; color: "#A8B8CA"; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                Repeater {
                    model: ["Dashboard", "Computers", "Users", "Connections", "History", "Settings"]
                    delegate: ActionButton {
                        text: modelData
                        secondary: page.section !== modelData
                        Layout.fillWidth: true
                        onClicked: page.section = modelData
                    }
                }
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#35475D" }
                ActionButton { objectName: "adminSignOutButton"; text: "Sign Out"; secondary: true; Layout.fillWidth: true; onClicked: root.confirmAdminSignOut() }
                ActionButton { text: "About"; secondary: page.section !== "About"; Layout.fillWidth: true; onClicked: page.section = "About" }
                Item { Layout.fillHeight: true }
                Text { text: (bridge.view.onlineCount || 0) + " / " + (bridge.view.totalCount || 0) + " PCs online"; color: "#A8B8CA" }
                Text { text: "LOCAL NETWORK"; color: "#64BCC1"; font.bold: true; font.pixelSize: 11 }
                Text { text: "Developed by Sumit Kumar · StickForYou"; color: "#8398AC"; font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.margins: 20; spacing: 15
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout { Layout.fillWidth: true
                    Text { text: page.section; color: "#F4F7FB"; font.pixelSize: 29; font.bold: true }
                    Text { text: bridge.view.cafeName || "GameGrid"; color: "#A8B8CA" }
                }
                Item { Layout.fillWidth: true }
                Text { text: bridge.busy ? "Working…" : "● Live"; color: "#64BCC1"; font.bold: true }
            }
            RowLayout {
                visible: page.section === "Dashboard"
                spacing: 12
                Repeater {
                    model: [{label:"Online PCs",value:bridge.view.onlineCount || 0},
                            {label:"Total PCs",value:bridge.view.totalCount || 0},
                            {label:"Unlock requests",value:bridge.view.unlockRequests || 0}]
                    Panel {
                        Layout.fillWidth: true; implicitHeight: 90
                        ColumnLayout { anchors.fill: parent; anchors.margins: 14
                            Text { text: modelData.label; color: "#A8B8CA"; font.pixelSize: 13 }
                            Text { text: modelData.value; color: "#F4F7FB"; font.pixelSize: 25; font.bold: true }
                        }
                        MouseArea { anchors.fill: parent; enabled: modelData.label === "Unlock requests"; onClicked: bridge.selectFirstUnlock() }
                    }
                }
            }
            RowLayout {
                visible: page.section === "Dashboard" || page.section === "Computers"
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 14
                Panel {
                    objectName: "computerManagementPanel"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "ALL COMPUTERS"; color: "#BED1DA"; font.bold: true; font.pixelSize: 12; Layout.fillWidth: true }
                            Text { text: "SORT BY"; color: "#8398AC"; font.bold: true; font.pixelSize: 10 }
                            DarkComboBox {
                                objectName: "computerSortBox"
                                model: ["Recent", "Name (A–Z)"]
                                currentIndex: bridge.view.computerSort === "name" ? 1 : 0
                                Layout.preferredWidth: 142
                                onActivated: bridge.setComputerSort(currentIndex === 1 ? "name" : "recent")
                                Accessible.name: "Sort computers"
                            }
                        }
                        ListView {
                            id: pcList
                            Layout.fillWidth: true; Layout.fillHeight: true
                            model: bridge.pcModel
                            clip: true; spacing: 10
                            delegate: Panel {
                                id: card
                                required property var rowData
                                width: pcList.width - 8
                                implicitHeight: bridge.expandedPcId === rowData.pcId ? detailColumn.implicitHeight + 24 : 90
                                color: rowData.unlockRequested ? "#374837" : bridge.selectedPcId === rowData.pcId ? "#29445B" : "#1B2B40"
                                border.color: bridge.selectedPcId === rowData.pcId ? theme.accent : rowData.unlockRequested ? theme.success : theme.panelBorder
                                Behavior on implicitHeight { NumberAnimation { duration: 170; easing.type: Easing.OutCubic } }
                                MouseArea { anchors.fill: parent; onClicked: bridge.selectPc(card.rowData.pcId) }
                                ColumnLayout {
                                    id: detailColumn
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                    anchors.margins: 12; spacing: 9
                                    RowLayout {
                                        Layout.fillWidth: true
                                        ColumnLayout { Layout.fillWidth: true
                                            Text { text: card.rowData.name; color: "#F4F7FB"; font.pixelSize: 17; font.bold: true }
                                            Text { text: card.rowData.role + "  ·  " + (card.rowData.online ? "ONLINE" : "OFFLINE") + "  ·  " + card.rowData.access; color: card.rowData.online ? "#A4B36A" : "#A8B8CA"; font.pixelSize: 11 }
                                        }
                                        Item { Layout.fillWidth: true }
                                        Text { objectName: "adminSessionTimer"; text: card.rowData.timeText; color: card.rowData.phase === "RETAINER" ? theme.accent : card.rowData.phase === "BUFFER" ? theme.buffer : card.rowData.phase === "PAUSED" ? theme.paused : card.rowData.phase === "GRACE" || card.rowData.phase === "EXPIRED" ? theme.grace : theme.active; font.pixelSize: 19; font.bold: true }
                                    }
                                    Text { text: card.rowData.player + "  ·  " + (card.rowData.kind === "none" ? "No session" : card.rowData.kind) + "  ·  " + card.rowData.detail; color: "#A8B8CA"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                    ColumnLayout {
                                        visible: bridge.expandedPcId === card.rowData.pcId
                                        Layout.fillWidth: true; spacing: 9
                                        Text { text: "PLAYER  " + card.rowData.player + "    ·    " + card.rowData.phase; color: "#F4F7FB"; font.pixelSize: 12 }
                                        Text { text: "Started " + card.rowData.startedText; color: "#A8B8CA"; visible: card.rowData.startedText !== ""; font.pixelSize: 12 }
                                        RowLayout {
                                            visible: card.rowData.start
                                            Text { text: "Initial player: " + (card.rowData.registeredCustomerName || "Guest"); color: "#A8B8CA"; font.pixelSize: 12 }
                                        }
                                        RowLayout {
                                            visible: card.rowData.start
                                            Text { text: "Session"; color: "#A8B8CA" }
                                            DarkComboBox { id: kindBox; model: ["Timed", "No timer"]; Layout.preferredWidth: 110 }
                                            DarkComboBox { id: paidField; model: ["15", "30", "60", "120", "Custom Minutes"]; currentIndex: 2; visible: kindBox.currentIndex === 0; Layout.preferredWidth: 145 }
                                            TextField { id: customPaid; visible: kindBox.currentIndex === 0 && paidField.currentIndex === 4; placeholderText: "Minutes"; Layout.preferredWidth: 88; inputMethodHints: Qt.ImhDigitsOnly }
                                            TextField { id: bufferField; text: "0"; placeholderText: "Buffer"; Layout.preferredWidth: 82; inputMethodHints: Qt.ImhDigitsOnly }
                                            ActionButton { text: "Start session"; onClicked: {
                                                let kind = kindBox.currentIndex === 0 ? "timed" : "open"
                                                let paid = paidField.currentIndex === 4 ? customPaid.text : paidField.currentText
                                                if ((kind === "timed" && !page.validMinutes(paid, false)) || !page.validMinutes(bufferField.text, true)) {
                                                    root.ask("Invalid session time", "Use whole minutes: paid time 1 to 1440 and buffer 0 to 1440.", "OK", function(){})
                                                    return
                                                }
                                                bridge.startSession(card.rowData.pcId, kind, paid, bufferField.text)
                                            } }
                                        }
                                        RowLayout {
                                            visible: card.rowData.add
                                            DarkComboBox { id: addBox; model: ["1","2","5","15","30","60","Custom Minutes"]; currentIndex: 3; Layout.preferredWidth: 145 }
                                            TextField { id: customAdd; visible: addBox.currentIndex === 6; placeholderText: "Minutes"; Layout.preferredWidth: 88; inputMethodHints: Qt.ImhDigitsOnly }
                                            ActionButton {
                                                text: "Add time"
                                                onClicked: {
                                                    var raw = addBox.currentIndex === 6 ? customAdd.text : addBox.currentText
                                                    if (!page.validMinutes(raw, false)) {
                                                        root.ask("Invalid time", "Use a whole number from 1 to 1440 minutes.", "OK", function(){})
                                                        return
                                                    }
                                                    var minutes = Number(raw)
                                                    var pcId = card.rowData.pcId
                                                    var preview = bridge.previewAddTime(pcId, minutes)
                                                    root.ask("Review added time", preview, "Continue", function(){
                                                        root.ask("Final confirmation", "Add " + minutes + " minutes to " + card.rowData.name + "?", "Confirm add", function(){ bridge.addTime(pcId, minutes) })
                                                    })
                                                }
                                            }
                                        }
                                        RowLayout {
                                            ActionButton { text: "Pause"; visible: card.rowData.pause; secondary: true; onClicked: bridge.pauseSession(card.rowData.pcId) }
                                            ActionButton { text: "Resume session"; visible: card.rowData.resume; onClicked: bridge.resumeSession(card.rowData.pcId) }
                                            ActionButton {
                                                text: card.rowData.end ? "End session" : "Lock PC"
                                                danger: true; visible: card.rowData.end || card.rowData.lock
                                                onClicked: {
                                                    var pcId = card.rowData.pcId
                                                    root.ask("Confirm lock", "Lock " + card.rowData.name + (card.rowData.end ? " and end " + card.rowData.player + "'s session?" : "?"), "Continue", function(){
                                                        root.ask("Final confirmation", "This will lock " + card.rowData.name + ".", "Lock PC", function(){bridge.endSession(pcId)})
                                                    })
                                                }
                                            }
                                            Text { text: "Unlock requested"; color: "#F2A65A"; visible: card.rowData.unlockRequested }
                                        }
                                        ActionButton { text: "Exit Software"; danger: true; visible: card.rowData.exitSoftware; onClicked: {
                                            var pcId = card.rowData.pcId
                                            root.ask("Exit software on " + card.rowData.name,
                                                "GameGrid will close on this PC" + (card.rowData.end ? " and its active session will end" : "") + ". The Windows desktop may then be accessible without cafe enforcement. Continue?",
                                                "Continue", function(){
                                                    if (card.rowData.end) {
                                                        root.ask("Final confirmation", "End the session on " + card.rowData.name + " and exit GameGrid?", "Exit Software", function(){ bridge.exitRemoteSoftware(pcId) })
                                                    } else {
                                                        bridge.exitRemoteSoftware(pcId)
                                                    }
                                                })
                                        } }
                                    }
                                }
                            }
                        }
                    }
                }
                Panel {
                    objectName: "recentSessionsPanel"
                    Layout.preferredWidth: Math.max(180, Math.min(235, page.width * 0.15))
                    Layout.minimumWidth: 180
                    Layout.maximumWidth: 235
                    Layout.fillHeight: true
                    ColumnLayout { anchors.fill: parent; anchors.margins: 15; spacing: 9
                        Item {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            Layout.preferredHeight: 3
                            ColumnLayout { anchors.fill: parent; spacing: 8
                                Text { text: "RECENT SESSIONS"; color: "#BED1DA"; font.bold: true; font.pixelSize: 12; Layout.fillWidth: true }
                                ListView { id: historySide; objectName: "recentSessionsList"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: bridge.dashboardHistoryModel; spacing: 8
                                    Text {
                                        objectName: "recentHistoryEmptyState"
                                        anchors.centerIn: parent
                                        width: parent.width - 16
                                        horizontalAlignment: Text.AlignHCenter
                                        wrapMode: Text.WordWrap
                                        text: bridge.selectedPcId ? "No sessions recorded for this PC." : "Select a computer to view recent sessions."
                                        color: "#8398AC"
                                        visible: historySide.count === 0
                                        font.pixelSize: 12
                                    }
                                    delegate: Rectangle { required property var rowData; width: historySide.width; height: 84; radius: 9; color: "#253C54"
                                        ColumnLayout { anchors.fill: parent; anchors.margins: 9; spacing: 3
                                            OverflowText { text: rowData.pcName; color: "#F4F7FB"; bold: true; Layout.fillWidth: true }
                                            OverflowText { text: rowData.player; color: "#A8B8CA"; pixelSize: 12; Layout.fillWidth: true }
                                            Text { text: rowData.duration; color: "#64BCC1"; font.pixelSize: 12; font.bold: true }
                                        }
                                    }
                                }
                            }
                        }
                        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#35475D" }
                        Item {
                            id: notificationsSection
                            objectName: "notificationsSection"
                            Layout.fillWidth: true; Layout.fillHeight: true
                            Layout.preferredHeight: 2
                            ColumnLayout { anchors.fill: parent; spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true
                                    Text { text: "NOTIFICATIONS"; color: "#BED1DA"; font.bold: true; font.pixelSize: 12 }
                                    Item { Layout.fillWidth: true }
                                    Rectangle {
                                        id: unreadIndicator
                                        objectName: "notificationUnreadIndicator"
                                        visible: (bridge.view.notificationUnreadCount || 0) > 0
                                        implicitWidth: Math.max(20, unreadCount.implicitWidth + 10)
                                        implicitHeight: 20
                                        radius: 10
                                        color: "#D85288"
                                        Text { id: unreadCount; anchors.centerIn: parent; text: bridge.view.notificationUnreadCount || 0; color: "white"; font.bold: true; font.pixelSize: 10 }
                                        SequentialAnimation on opacity {
                                            running: unreadIndicator.visible
                                            loops: Animation.Infinite
                                            NumberAnimation { to: 0.45; duration: 650; easing.type: Easing.InOutSine }
                                            NumberAnimation { to: 1.0; duration: 650; easing.type: Easing.InOutSine }
                                        }
                                    }
                                }
                                ListView {
                                    id: notificationList
                                    objectName: "notificationList"
                                    Layout.fillWidth: true; Layout.fillHeight: true
                                    model: bridge.notificationModel
                                    clip: true; spacing: 7
                                    onCountChanged: positionViewAtBeginning()
                                    Connections {
                                        target: bridge.notificationModel
                                        function onModelReset() { notificationList.positionViewAtBeginning() }
                                    }
                                    delegate: Rectangle {
                                        required property var rowData
                                        width: notificationList.width
                                        height: 58; radius: 8
                                        color: rowData.unread ? "#29445B" : "#1B2B40"
                                        RowLayout {
                                            anchors.fill: parent; anchors.margins: 8; spacing: 7
                                            Rectangle {
                                                width: 7; height: 7; radius: 4
                                                color: rowData.eventType === "online" ? "#64BCC1" : rowData.eventType === "offline" ? "#F2A65A" : "#8398AC"
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true; spacing: 2
                                                OverflowText { text: rowData.title; color: "#F4F7FB"; bold: rowData.unread; Layout.fillWidth: true }
                                                OverflowText { text: rowData.detail; color: "#8398AC"; pixelSize: 10; Layout.fillWidth: true }
                                            }
                                            Text { text: rowData.timeText; color: "#8398AC"; font.pixelSize: 10; Layout.alignment: Qt.AlignTop }
                                        }
                                    }
                                    Text {
                                        anchors.centerIn: parent
                                        visible: notificationList.count === 0
                                        text: "No notifications yet."
                                        color: "#8398AC"; font.pixelSize: 11
                                    }
                                }
                            }
                            TapHandler { onTapped: bridge.acknowledgeNotifications() }
                        }
                    }
                }
            }
            Loader { Layout.fillWidth: true; Layout.fillHeight: active; visible: active; active: page.section === "Connections"; sourceComponent: connections }
            Loader { Layout.fillWidth: true; Layout.fillHeight: active; visible: active; active: page.section === "Users"; sourceComponent: users }
            Loader { Layout.fillWidth: true; Layout.fillHeight: active; visible: active; active: page.section === "History"; sourceComponent: historyFull }
            Loader { Layout.fillWidth: true; Layout.fillHeight: active; visible: active; active: page.section === "Settings"; sourceComponent: settings }
            Loader { Layout.fillWidth: true; Layout.fillHeight: active; visible: active; active: page.section === "About"; sourceComponent: about }
        }
    }
    Component {
        id: users
        RowLayout {
            spacing: 14
            Panel {
                objectName: "customerListPanel"
                Layout.preferredWidth: 330
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10
                    Text { text: "CUSTOMERS"; color: "#BED1DA"; font.bold: true; font.pixelSize: 12 }
                    TextField {
                        objectName: "customerSearchField"
                        placeholderText: "Search customer name"
                        Layout.fillWidth: true
                        onTextChanged: bridge.setCustomerSearch(text)
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        TextField {
                            id: newCustomerName
                            objectName: "newCustomerNameField"
                            placeholderText: "Customer Name"
                            Layout.fillWidth: true
                        }
                        ActionButton {
                            objectName: "createCustomerButton"
                            text: "Create User"
                            onClicked: {
                                let name = bridge.normalizeCustomerName(newCustomerName.text)
                                if (!name) {
                                    root.ask("Customer name required",
                                             "Enter a customer name before creating the account.",
                                             "OK", function(){})
                                    return
                                }
                                root.ask("Create Customer",
                                         "Name: " + name
                                         + "\n\nDefault Password: 12345"
                                         + "\n\nRetainer Balance: 00:00:00",
                                         "Confirm Create", function() {
                                             bridge.createCustomer(name)
                                             newCustomerName.text = ""
                                         })
                            }
                        }
                    }
                    Text {
                        text: (bridge.view.customerCount || 0) + " registered users"
                        color: "#8398AC"
                        font.pixelSize: 11
                    }
                    ListView {
                        id: customerList
                        objectName: "customerList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 7
                        model: bridge.customerModel
                        Text {
                            anchors.centerIn: parent
                            width: parent.width - 20
                            text: "No customers found"
                            horizontalAlignment: Text.AlignHCenter
                            color: "#8398AC"
                            visible: customerList.count === 0
                        }
                        delegate: Rectangle {
                            required property var rowData
                            width: customerList.width
                            height: 72
                            radius: 9
                            color: bridge.selectedCustomerId === rowData.customerId ? "#29445B" : "#1B2B40"
                            border.color: bridge.selectedCustomerId === rowData.customerId ? theme.accent : theme.panelBorder
                            MouseArea { anchors.fill: parent; onClicked: bridge.selectCustomer(parent.rowData.customerId) }
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 10; spacing: 3
                                RowLayout { Layout.fillWidth: true
                                    Text { text: rowData.name; color: "#F4F7FB"; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Text { text: rowData.balanceText; color: "#D85288"; font.bold: true }
                                }
                                Text { text: rowData.status + " · " + rowData.syncStatus; color: "#A8B8CA"; font.pixelSize: 11 }
                            }
                        }
                    }
                }
            }
            Panel {
                objectName: "customerDetailsPanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Text {
                    anchors.centerIn: parent
                    text: "Select a customer to view account details."
                    color: "#8398AC"
                    visible: !bridge.view.selectedCustomer || !bridge.view.selectedCustomer.customerId
                }
                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 16
                    contentWidth: availableWidth
                    visible: !!bridge.view.selectedCustomer && !!bridge.view.selectedCustomer.customerId
                    ColumnLayout {
                        width: parent.width
                        spacing: 12
                        Text { text: bridge.view.selectedCustomer ? bridge.view.selectedCustomer.name : ""; color: "#F4F7FB"; font.pixelSize: 24; font.bold: true }
                        Text { text: bridge.view.selectedCustomer ? "Customer ID  " + bridge.view.selectedCustomer.customerId : ""; color: "#8398AC"; font.pixelSize: 11; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                        Text { text: bridge.view.selectedCustomer ? bridge.view.selectedCustomer.balanceText : "00:00:00"; color: "#D85288"; font.pixelSize: 34; font.bold: true }
                        Text { text: "RETAINER BALANCE"; color: "#BED1DA"; font.pixelSize: 11; font.bold: true }
                        RowLayout {
                            Layout.fillWidth: true
                            ColumnLayout { Layout.fillWidth: true
                                Text { text: "Add Retainer Time"; color: "#F4F7FB"; font.bold: true }
                                RowLayout { Layout.fillWidth: true
                                    TextField { id: addHours; objectName: "retainerAddHours"; placeholderText: "Hours"; inputMethodHints: Qt.ImhDigitsOnly; Layout.fillWidth: true; enabled: !bridge.view.selectedCustomer.retainerInProgress }
                                    TextField { id: addMinutes; objectName: "retainerAddMinutes"; placeholderText: "Minutes"; inputMethodHints: Qt.ImhDigitsOnly; Layout.fillWidth: true; enabled: !bridge.view.selectedCustomer.retainerInProgress }
                                    ActionButton { objectName: "addRetainerButton"; text: "Add Time"; customFill: true; customColor: theme.success; enabled: !bridge.view.selectedCustomer.retainerInProgress; onClicked: page.reviewRetainerAdjustment(bridge.previewCustomerAdjustment(bridge.selectedCustomerId, "add", addHours.text, addMinutes.text)) }
                                }
                            }
                            ColumnLayout { Layout.fillWidth: true
                                Text { text: "Reduce Retainer Time"; color: "#F4F7FB"; font.bold: true }
                                RowLayout { Layout.fillWidth: true
                                    TextField { id: reduceHours; objectName: "retainerReduceHours"; placeholderText: "Hours"; inputMethodHints: Qt.ImhDigitsOnly; Layout.fillWidth: true; enabled: !bridge.view.selectedCustomer.retainerInProgress }
                                    TextField { id: reduceMinutes; objectName: "retainerReduceMinutes"; placeholderText: "Minutes"; inputMethodHints: Qt.ImhDigitsOnly; Layout.fillWidth: true; enabled: !bridge.view.selectedCustomer.retainerInProgress }
                                    ActionButton { objectName: "reduceRetainerButton"; text: "Reduce Time"; danger: true; enabled: !bridge.view.selectedCustomer.retainerInProgress; onClicked: page.reviewRetainerAdjustment(bridge.previewCustomerAdjustment(bridge.selectedCustomerId, "reduce", reduceHours.text, reduceMinutes.text)) }
                                }
                            }
                        }
                        Text {
                            objectName: "activeRetainerAdjustmentMessage"
                            text: "Balance adjustments are unavailable during an active retainer session. End the session before making changes."
                            visible: bridge.view.selectedCustomer && bridge.view.selectedCustomer.retainerInProgress
                            color: theme.grace; wrapMode: Text.WordWrap; Layout.fillWidth: true
                        }
                        Text { text: "CUSTOMER COOLDOWN OVERRIDES"; color: "#BED1DA"; font.pixelSize: 11; font.bold: true }
                        Text {
                            text: "Leave blank to use the café-wide setting. Enter 0 to disable a cooldown for this customer."
                            color: "#8398AC"; font.pixelSize: 11
                            wrapMode: Text.WordWrap; Layout.fillWidth: true
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            TextField {
                                id: customerRetainerCooldown
                                objectName: "customerRetainerCooldownOverride"
                                placeholderText: "Retainer · Use Global Setting"
                                text: bridge.view.selectedCustomer
                                      && bridge.view.selectedCustomer.retainerCooldownOverride !== null
                                      && bridge.view.selectedCustomer.retainerCooldownOverride !== undefined
                                      ? String(bridge.view.selectedCustomer.retainerCooldownOverride) : ""
                                inputMethodHints: Qt.ImhDigitsOnly
                                Layout.fillWidth: true
                            }
                            TextField {
                                id: customerLoginCooldown
                                objectName: "customerLoginCooldownOverride"
                                placeholderText: "Login · Use Global Setting"
                                text: bridge.view.selectedCustomer
                                      && bridge.view.selectedCustomer.loginCooldownOverride !== null
                                      && bridge.view.selectedCustomer.loginCooldownOverride !== undefined
                                      ? String(bridge.view.selectedCustomer.loginCooldownOverride) : ""
                                inputMethodHints: Qt.ImhDigitsOnly
                                Layout.fillWidth: true
                            }
                            ActionButton {
                                objectName: "saveCustomerCooldownOverridesButton"
                                text: "Save Overrides"
                                onClicked: bridge.saveCustomerCooldownOverrides(
                                    bridge.view.selectedCustomer.customerId,
                                    customerRetainerCooldown.text,
                                    customerLoginCooldown.text,
                                    bridge.view.selectedCustomer.revision)
                            }
                        }
                        Text {
                            text: bridge.view.selectedCustomer
                                  ? "Effective: Retainer " + bridge.view.selectedCustomer.retainerCooldownMinutes
                                    + " min · Login " + bridge.view.selectedCustomer.loginCooldownMinutes + " min"
                                  : ""
                            color: "#A8B8CA"; font.pixelSize: 11
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            ActionButton {
                                objectName: "clearRetainerCooldownButton"
                                text: "Clear Retainer Cooldown"
                                secondary: true
                                visible: bridge.view.selectedCustomer
                                         && bridge.view.selectedCustomer.retainerCooldownExpiresAt > page.currentUtc
                                onClicked: bridge.clearCustomerCooldown(
                                    bridge.view.selectedCustomer.customerId, "retainer")
                            }
                            Text {
                                visible: bridge.view.selectedCustomer
                                         && bridge.view.selectedCustomer.retainerCooldownExpiresAt > page.currentUtc
                                text: bridge.view.selectedCustomer
                                      ? page.cooldownText(Math.max(0,
                                            bridge.view.selectedCustomer.retainerCooldownExpiresAt - page.currentUtc))
                                      : ""
                                color: theme.grace
                            }
                            ActionButton {
                                objectName: "clearLoginCooldownButton"
                                text: "Clear Login Cooldown"
                                secondary: true
                                visible: bridge.view.selectedCustomer
                                         && bridge.view.selectedCustomer.loginCooldownExpiresAt > page.currentUtc
                                onClicked: bridge.clearCustomerCooldown(
                                    bridge.view.selectedCustomer.customerId, "login")
                            }
                            Text {
                                visible: bridge.view.selectedCustomer
                                         && bridge.view.selectedCustomer.loginCooldownExpiresAt > page.currentUtc
                                text: bridge.view.selectedCustomer
                                      ? page.cooldownText(Math.max(0,
                                            bridge.view.selectedCustomer.loginCooldownExpiresAt - page.currentUtc))
                                      : ""
                                color: theme.grace
                            }
                        }
                        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#35475D" }
                        RowLayout { Layout.fillWidth: true
                            ColumnLayout { Layout.fillWidth: true
                                Text { text: bridge.view.selectedCustomer ? "Created  " + bridge.view.selectedCustomer.createdText : ""; color: "#A8B8CA"; font.pixelSize: 12 }
                                Text { text: bridge.view.selectedCustomer ? "Modified  " + bridge.view.selectedCustomer.modifiedText + " by " + bridge.view.selectedCustomer.modifiedBy : ""; color: "#A8B8CA"; font.pixelSize: 12 }
                            }
                            Text { text: bridge.view.selectedCustomer ? bridge.view.selectedCustomer.syncStatus : ""; color: "#64BCC1"; font.bold: true }
                        }
                        Text { text: "SYNCHRONIZATION"; color: "#BED1DA"; font.bold: true; font.pixelSize: 12 }
                        Repeater {
                            model: bridge.view.selectedCustomer ? bridge.view.selectedCustomer.syncPeers || [] : []
                            RowLayout { Layout.fillWidth: true
                                Text { text: modelData.name; color: "#A8B8CA"; Layout.fillWidth: true }
                                Text { text: modelData.status; color: modelData.status === "Synced" ? theme.success : modelData.status === "Error" ? theme.danger : theme.grace }
                            }
                        }
                        Text { text: "RETAINER ADJUSTMENT HISTORY"; color: "#BED1DA"; font.bold: true; font.pixelSize: 12 }
                        Text { text: "No retainer adjustments recorded."; color: "#8398AC"; visible: (bridge.view.customerAdjustmentCount || 0) === 0 }
                        Repeater {
                            model: bridge.customerAdjustmentModel
                            Rectangle { required property var rowData; Layout.fillWidth: true; implicitHeight: 58; radius: 8; color: "#203348"
                                RowLayout { anchors.fill: parent; anchors.margins: 10
                                    ColumnLayout { Layout.fillWidth: true
                                        Text { text: rowData.action + " " + rowData.durationText; color: rowData.action === "Add" ? theme.success : theme.danger; font.bold: true }
                                        Text { text: rowData.createdText + " · " + rowData.adminName; color: "#8398AC"; font.pixelSize: 11 }
                                    }
                                    Text { text: rowData.previousText + " → " + rowData.resultingText; color: "#F4F7FB" }
                                }
                            }
                        }
                        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#35475D" }
                        ActionButton {
                            objectName: "releaseCustomerLoginButton"
                            text: "Release Abandoned Login"
                            secondary: true
                            visible: bridge.view.selectedCustomer && !!bridge.view.selectedCustomer.loggedInPcId
                            Layout.alignment: Qt.AlignRight
                            onClicked: root.ask(
                                "Release customer login?",
                                "Use this only when " + bridge.view.selectedCustomer.name
                                + " can no longer log out from the previous PC. Any active access on that PC will be locked.",
                                "Release Login", function(){
                                    bridge.releaseCustomerLogin(
                                        bridge.view.selectedCustomer.customerId)
                                })
                        }
                        ActionButton {
                            objectName: "deleteCustomerButton"
                            text: "Delete User"
                            danger: true
                            Layout.alignment: Qt.AlignRight
                            onClicked: page.confirmCustomerDeletion(bridge.view.selectedCustomer)
                        }
                    }
                }
            }
        }
    }
    Component {
        id: connections
        ScrollView { contentWidth: availableWidth
            ColumnLayout { width: parent.width; spacing: 14
                Panel { Layout.fillWidth: true; implicitHeight: 72; color: "#29445B"
                    Column { anchors.fill: parent; anchors.margins: 14; spacing: 5
                        Text { text: "PENDING JOIN REQUESTS"; color: "#F4F7FB"; font.bold: true; font.pixelSize: 17 }
                        Text { text: (bridge.view.pendingCount || 0) + " awaiting approval"; color: "#64BCC1" }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: 70; visible: (bridge.view.pendingCount || 0) === 0; color: "#1B2B40"
                    Text { anchors.centerIn: parent; text: "No pending join requests"; color: "#A8B8CA" }
                }
                Repeater { model: bridge.joinModel
                    Panel { required property var rowData; Layout.fillWidth: true; implicitHeight: 110
                        RowLayout { anchors.fill: parent; anchors.margins: 15
                            ColumnLayout { Layout.fillWidth: true
                                Text { text: rowData.name; color: "#F4F7FB"; font.bold: true }
                                Text { text: rowData.ip + " · " + rowData.pcId; color: "#A8B8CA"; font.pixelSize: 11 }
                            }
                            TextField { id: code; objectName: "pairingCodeInput"; placeholderText: "6-digit code"; Layout.preferredWidth: 125; maximumLength: 6; inputMethodHints: Qt.ImhDigitsOnly }
                            TextField { id: approvedName; text: rowData.name; Layout.preferredWidth: 145 }
                            ActionButton { objectName: "verifyAndConnectButton"; text: "Verify & Connect"; onClicked: {
                                let requestId = rowData.requestId
                                let pcId = rowData.pcId
                                let enteredCode = code.text
                                let name = approvedName.text
                                root.ask("Connect computer", "Verify the code supplied by " + name + " and connect this PC?", "Verify & Connect", function(){bridge.approvePairing(requestId,pcId,enteredCode,name)})
                            } }
                        }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: 72; color: "#29445B"
                    Column { anchors.fill: parent; anchors.margins: 14; spacing: 5
                        Text { text: "REGISTERED COMPUTERS"; color: "#F4F7FB"; font.bold: true; font.pixelSize: 17 }
                        Text { text: (bridge.view.totalCount || 0) + " known devices"; color: "#64BCC1" }
                    }
                }
                Repeater { model: bridge.memberModel
                    Panel { required property var rowData; Layout.fillWidth: true; implicitHeight: 72
                        RowLayout { anchors.fill: parent; anchors.margins: 12
                            Text { text: rowData.online ? "●" : "○"; color: rowData.online ? "#64BCC1" : "#A8B8CA" }
                            Text { text: rowData.name; color: "#F4F7FB"; Layout.fillWidth: true }
                            TextField { id: newName; placeholderText: "Rename PC"; Layout.preferredWidth: 170 }
                            ActionButton { text: "Save name"; secondary: true; onClicked: bridge.renamePc(rowData.pcId,newName.text) }
                        }
                    }
                }
            }
        }
    }
    Component { id: historyFull
        Panel { ColumnLayout { anchors.fill: parent; anchors.margins: 16
            RowLayout { Text { text: "Session history"; color: "#F4F7FB"; font.pixelSize: 19; Layout.fillWidth: true }
                ActionButton { objectName: "allHistoryButton"; text: "All PCs"; secondary: true; onClicked: bridge.showAllHistory() }
            }
            ListView { id: fullHistory; Layout.fillWidth: true; Layout.fillHeight: true; model: bridge.historyModel; clip: true; spacing: 7
                Text { anchors.centerIn: parent; text: "No completed sessions yet"; color: "#8398AC"; visible: fullHistory.count === 0 }
                delegate: Rectangle { required property var rowData; width: fullHistory.width; height: 72; radius: 8; color: "#253C54"
                    RowLayout { anchors.fill: parent; anchors.margins: 12
                        ColumnLayout { Layout.fillWidth: true
                            Text { text: rowData.pcName + "  ·  " + rowData.player; color: "#F4F7FB"; font.bold: true }
                            Text { text: rowData.started + " → " + rowData.ended; color: "#A8B8CA"; font.pixelSize: 11 }
                        }
                        Text { text: rowData.kind + " · " + rowData.duration + " · " + (rowData.kind === "timed" ? rowData.paidMinutes + " paid min · " : "") + rowData.reason; color: "#64BCC1" }
                    }
                }
            }
        } }
    }
    Component { id: settings
        ScrollView { contentWidth: availableWidth
            ColumnLayout { width: Math.min(parent.width, 700); spacing: 15
                Panel { Layout.fillWidth: true; implicitHeight: historyManagement.implicitHeight + 34
                    ColumnLayout { id: historyManagement; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "History Management"; color: "#F4F7FB"; font.pixelSize: 19; font.bold: true }
                        DarkComboBox {
                            id: historyPc
                            objectName: "historyPcSelector"
                            Layout.fillWidth: true
                            model: bridge.view.historyTargets || []
                            textRole: "name"
                            displayText: page.historyTarget() ? page.historyTarget().name : "Select a computer"
                            onActivated: {
                                page.historyTargetId = model[currentIndex].pcId
                                page.historyPendingId = ""
                                root.cancelConfirmation()
                            }
                        }
                        Text { text: page.historyTarget() ? (page.historyTarget().count > 0 ? page.historyTarget().name + " · " + page.historyTarget().count + " completed sessions" : "No completed sessions for this PC.") : "Choose one registered computer."; color: "#A8B8CA" }
                        Text { visible: !!page.historyTarget() && !page.historyTarget().online; text: "This PC must reconnect before its history can be cleared."; color: "#F2A65A"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        ActionButton {
                            objectName: "clearSelectedHistory"
                            text: "Clear History"
                            danger: true
                            enabled: !!page.historyTarget() && page.historyTarget().online && page.historyTarget().count > 0 && !bridge.busy
                            onClicked: {
                                let target = page.historyTarget()
                                if (!target) return
                                let pcId = target.pcId
                                let pcName = target.name
                                page.historyPendingId = pcId
                                root.ask("Clear history for " + pcName,
                                         "Only this PC's completed session history will be removed. Its active session and other PCs will not be changed.",
                                         "Continue", function() {
                                    if (page.historyPendingId !== pcId || page.historyTargetId !== pcId) return
                                    root.ask("Final history deletion confirmation",
                                             "Permanently delete all completed session history for " + pcName + "? This cannot be undone.",
                                             "Confirm Delete", function() {
                                        if (page.historyPendingId === pcId && page.historyTargetId === pcId)
                                            bridge.clearHistory(pcId)
                                        page.historyPendingId = ""
                                    })
                                })
                            }
                        }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: identity.implicitHeight + 34
                    ColumnLayout { id: identity; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Cafe identity"; color: "#F4F7FB"; font.pixelSize: 19; font.bold: true }
                        TextField { id: cafeName; text: bridge.view.settings ? bridge.view.settings.cafeName : ""; placeholderText: "Cafe name"; Layout.fillWidth: true }
                        TextField { id: adminName; text: bridge.view.settings ? bridge.view.settings.adminName : ""; placeholderText: "Admin name"; Layout.fillWidth: true }
                        RowLayout { TextField { id: grace; text: bridge.view.settings ? String(bridge.view.settings.graceMinutes) : "10"; placeholderText: "Grace min"; Layout.fillWidth: true }
                            TextField { id: signout; text: bridge.view.settings ? String(bridge.view.settings.signoutMinutes) : "0"; placeholderText: "Auto signout min"; Layout.fillWidth: true }
                        }
                        Text { text: "Automatic Windows sign-out is disabled pending recovery design."; color: "#A8B8CA"; font.pixelSize: 11 }
                        ActionButton { text: "Save settings"; onClicked: bridge.saveSettings(cafeName.text,adminName.text,grace.text,signout.text) }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: customerAccessSettings.implicitHeight + 34
                    ColumnLayout { id: customerAccessSettings; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Customer Access Settings"; color: "#F4F7FB"; font.pixelSize: 19; font.bold: true }
                        Text {
                            text: "Applies café-wide unless a customer has an override. Enter 0 to disable a cooldown."
                            color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true
                                Text { text: "Retainer Restart Cooldown"; color: "#BED1DA"; font.pixelSize: 11 }
                                TextField {
                                    id: retainerRestartCooldown
                                    objectName: "retainerRestartCooldownMinutes"
                                    text: bridge.view.settings ? String(bridge.view.settings.retainerCooldownMinutes) : "0"
                                    placeholderText: "Minutes"
                                    inputMethodHints: Qt.ImhDigitsOnly
                                    Layout.fillWidth: true
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                Text { text: "Customer Login Cooldown"; color: "#BED1DA"; font.pixelSize: 11 }
                                TextField {
                                    id: customerLoginCooldownSetting
                                    objectName: "customerLoginCooldownMinutes"
                                    text: bridge.view.settings ? String(bridge.view.settings.loginCooldownMinutes) : "0"
                                    placeholderText: "Minutes"
                                    inputMethodHints: Qt.ImhDigitsOnly
                                    Layout.fillWidth: true
                                }
                            }
                            ActionButton {
                                objectName: "saveCustomerAccessSettingsButton"
                                text: "Save Cooldowns"
                                onClicked: bridge.saveCustomerAccessSettings(
                                    retainerRestartCooldown.text,
                                    customerLoginCooldownSetting.text)
                            }
                        }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: avatarSettings.implicitHeight + 34
                    ColumnLayout { id: avatarSettings; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Cafe avatar"; color: "#F4F7FB"; font.pixelSize: 19; font.bold: true }
                        RowLayout {
                            AvatarPreview {
                                objectName: "avatarPreview"
                                source: bridge.avatarPreviewSource(avatarPath.text)
                                crop: avatarFit.currentIndex === 1
                                Layout.preferredWidth: 184
                                Layout.preferredHeight: 184
                                Layout.minimumWidth: 184
                                Layout.maximumWidth: 184
                                Layout.minimumHeight: 184
                                Layout.maximumHeight: 184
                            }
                            ColumnLayout {
                                Text { text: "PNG or JPG · up to 5 MB"; color: "#A8B8CA" }
                                Text { text: "Preview fit"; color: "#A8B8CA"; font.pixelSize: 12 }
                                DarkComboBox { id: avatarFit; objectName: "avatarPreviewFit"; model: ["Fit", "Fill"] }
                            }
                        }
                        TextField { id: avatarPath; placeholderText: "Image file path (or drop file here)"; Layout.fillWidth: true }
                        RowLayout { ActionButton { text: "Browse / Choose Image"; secondary: true; onClicked: { var selected = bridge.browseAvatar(); if (selected) avatarPath.text = selected } }
                            ActionButton { text: "Save avatar"; onClicked: bridge.setAvatar(avatarPath.text) }
                            ActionButton { text: "Remove"; secondary: true; onClicked: bridge.removeAvatar() }
                        }
                        DropArea { Layout.fillWidth: true; Layout.preferredHeight: 48
                            onDropped: function(drop) { if (drop.hasUrls && drop.urls.length) avatarPath.text = drop.urls[0] }
                            Rectangle { anchors.fill: parent; radius: 8; color: "#203348"; border.color: "#455C73"
                                Text { anchors.centerIn: parent; text: "Drop a PNG or JPG here"; color: "#A8B8CA" }
                            }
                        }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: wallpaperSettings.implicitHeight + 34
                    ColumnLayout { id: wallpaperSettings; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Locked Console Wallpaper"; color: "#F4F7FB"; font.pixelSize: 19; font.bold: true }
                        Text {
                            text: "Shown only behind the locked CafeConsole. Images are optimized and shared across the local café network."
                            color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true
                        }
                        Rectangle {
                            objectName: "wallpaperPreview"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 230
                            radius: 10; color: "#0B1120"; clip: true
                            border.color: "#455C73"
                            Image {
                                anchors.fill: parent; anchors.margins: 1
                                source: wallpaperPath.text.length > 0
                                        ? bridge.wallpaperPreviewSource(wallpaperPath.text)
                                        : bridge.view.wallpaperSource || ""
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true; cache: false
                            }
                            Text {
                                anchors.centerIn: parent
                                text: "Default GameGrid background"
                                color: "#8398AC"
                                visible: wallpaperPath.text.length === 0
                                         && !bridge.view.wallpaperConfigured
                            }
                        }
                        TextField {
                            id: wallpaperPath
                            objectName: "wallpaperPath"
                            placeholderText: "PNG or JPG image file"
                            Layout.fillWidth: true
                        }
                        RowLayout {
                            ActionButton { text: "Browse / Choose Image"; secondary: true; onClicked: { var selected = bridge.browseWallpaper(); if (selected) wallpaperPath.text = selected } }
                            ActionButton { objectName: "saveWallpaperButton"; text: "Save Wallpaper"; enabled: wallpaperPath.text.length > 0; onClicked: bridge.setWallpaper(wallpaperPath.text) }
                            ActionButton { objectName: "resetWallpaperButton"; text: "Reset to Default"; secondary: true; enabled: bridge.view.wallpaperConfigured; onClicked: bridge.resetWallpaper() }
                        }
                        DropArea {
                            Layout.fillWidth: true; Layout.preferredHeight: 48
                            onDropped: function(drop) { if (drop.hasUrls && drop.urls.length) wallpaperPath.text = drop.urls[0] }
                            Rectangle { anchors.fill: parent; radius: 8; color: "#203348"; border.color: "#455C73"
                                Text { anchors.centerIn: parent; text: "Drop a PNG or JPG here"; color: "#A8B8CA" }
                            }
                        }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: security.implicitHeight + 34
                    ColumnLayout { id: security; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Admin security"; color: "#F4F7FB"; font.pixelSize: 19; font.bold: true }
                        TextField { id: oldPassword; placeholderText: "Current password"; echoMode: TextInput.Password; Layout.fillWidth: true }
                        TextField { id: newPassword; placeholderText: "New password"; echoMode: TextInput.Password; Layout.fillWidth: true }
                        TextField { id: confirmPassword; placeholderText: "Confirm new password"; echoMode: TextInput.Password; Layout.fillWidth: true }
                        ActionButton { text: "Change password"; onClicked: bridge.changePassword(oldPassword.text,newPassword.text,confirmPassword.text) }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: move.implicitHeight + 34
                    ColumnLayout { id: move; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Change cafe"; color: "#F4F7FB"; font.pixelSize: 19; font.bold: true }
                        Text { text: "Discover and request to join another cafe. Your old cafe remains until approval."; color: "#A8B8CA"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        TextField { id: movePassword; placeholderText: "Admin password"; echoMode: TextInput.Password; Layout.fillWidth: true }
                        ActionButton { text: "Find cafes"; onClicked: bridge.searchOtherPools(movePassword.text) }
                        Repeater { model: bridge.view.otherPools || []
                            ActionButton { text: "Request " + modelData.cafeName; secondary: true; onClicked: bridge.moveToPool(modelData.poolId) }
                        }
                    }
                }
                ActionButton { text: "Close software"; danger: true; onClicked: root.confirmAdminExit() }
            }
        }
    }
    Component { id: about
        Panel { ColumnLayout { anchors.fill: parent; anchors.margins: 24; spacing: 12
            RowLayout {
                spacing: 18
                BrandLogo { Layout.preferredWidth: 180; Layout.preferredHeight: 180 }
                Avatar { diameter: 70 }
            }
            Text { text: "GameGrid"; color: theme.text; font.pixelSize: 25; font.bold: true }
            Text { text: "A local-first gaming cafe management console."; color: "#A8B8CA" }
            Text { text: "Developed by " + bridge.view.developer + " · " + bridge.view.brand; color: "#F4F7FB" }
            Text { text: bridge.view.website; color: "#64BCC1"; MouseArea { anchors.fill: parent; onClicked: Qt.openUrlExternally(bridge.view.website) } }
            Text { text: bridge.view.email; color: "#64BCC1"; MouseArea { anchors.fill: parent; onClicked: Qt.openUrlExternally("mailto:" + bridge.view.email) } }
            Item { Layout.fillHeight: true }
        } }
    }
}
