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
                    model: ["Dashboard", "Computers", "Connections", "History", "Settings", "About"]
                    delegate: ActionButton {
                        text: modelData
                        secondary: page.section !== modelData
                        Layout.fillWidth: true
                        onClicked: page.section = modelData
                    }
                }
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
                                        Text { objectName: "adminSessionTimer"; text: card.rowData.timeText; color: card.rowData.phase === "BUFFER" ? theme.buffer : card.rowData.phase === "PAUSED" ? theme.paused : card.rowData.phase === "GRACE" || card.rowData.phase === "EXPIRED" ? theme.grace : theme.active; font.pixelSize: 19; font.bold: true }
                                    }
                                    Text { text: card.rowData.player + "  ·  " + (card.rowData.kind === "none" ? "No session" : card.rowData.kind) + "  ·  " + card.rowData.detail; color: "#A8B8CA"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                    ColumnLayout {
                                        visible: bridge.expandedPcId === card.rowData.pcId
                                        Layout.fillWidth: true; spacing: 9
                                        Text { text: "PLAYER  " + card.rowData.player + "    ·    " + card.rowData.phase; color: "#F4F7FB"; font.pixelSize: 12 }
                                        Text { text: "Started " + card.rowData.startedText; color: "#A8B8CA"; visible: card.rowData.startedText !== ""; font.pixelSize: 12 }
                                        RowLayout {
                                            visible: card.rowData.start
                                            Text { text: "Initial player: Guest"; color: "#A8B8CA"; font.pixelSize: 12 }
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
                    ColumnLayout { anchors.fill: parent; anchors.margins: 15; spacing: 10
                        Text { text: "RECENT SESSIONS"; color: "#BED1DA"; font.bold: true; font.pixelSize: 12; Layout.fillWidth: true }
                        ListView { id: historySide; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: bridge.dashboardHistoryModel; spacing: 8
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
                                Column { anchors.fill: parent; anchors.margins: 9; spacing: 3
                                    Text { text: rowData.pcName; color: "#F4F7FB"; font.bold: true }
                                    Text { text: rowData.player; color: "#A8B8CA"; font.pixelSize: 12 }
                                    Text { text: rowData.duration; color: "#64BCC1"; font.pixelSize: 12; font.bold: true }
                                }
                            }
                        }
                    }
                }
            }
            Loader { Layout.fillWidth: true; Layout.fillHeight: active; visible: active; active: page.section === "Connections"; sourceComponent: connections }
            Loader { Layout.fillWidth: true; Layout.fillHeight: active; visible: active; active: page.section === "History"; sourceComponent: historyFull }
            Loader { Layout.fillWidth: true; Layout.fillHeight: active; visible: active; active: page.section === "Settings"; sourceComponent: settings }
            Loader { Layout.fillWidth: true; Layout.fillHeight: active; visible: active; active: page.section === "About"; sourceComponent: about }
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
                            TextField { id: code; placeholderText: "7-character code"; Layout.preferredWidth: 125; maximumLength: 7 }
                            TextField { id: approvedName; text: rowData.name; Layout.preferredWidth: 145 }
                            ActionButton { text: "Pair"; onClicked: bridge.preparePairing(rowData.requestId) }
                            ActionButton { text: "Approve"; onClicked: {
                                let requestId = rowData.requestId
                                let pcId = rowData.pcId
                                let enteredCode = code.text
                                let name = approvedName.text
                                root.ask("Approve computer", "Approve " + name + " using the code verified on that PC?", "Approve", function(){bridge.approvePairing(requestId,pcId,enteredCode,name)})
                            } }
                            ActionButton { text: "Reject"; danger: true; onClicked: bridge.rejectPairing(rowData.requestId) }
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
