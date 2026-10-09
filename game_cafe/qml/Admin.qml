import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property string section: "Dashboard"
    property string startKind: "timed"
    property string addValue: "15"
    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.preferredWidth: 220
            Layout.fillHeight: true
            color: "#142235"
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 18; spacing: 16
                RowLayout { Avatar { diameter: 48 } Text { text: "GAME CAFE\nCONSOLE"; color: "#f4fbfd"; font.bold: true; font.pixelSize: 16 } }
                Text { text: bridge.view.cafeName || "Cafe"; color: "#89a5b8"; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
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
                Text { text: (bridge.view.onlineCount || 0) + " / " + (bridge.view.totalCount || 0) + " PCs online"; color: "#91a7ba" }
                Text { text: "LOCAL NETWORK"; color: "#35c7c7"; font.bold: true; font.pixelSize: 11 }
                Text { text: "Developed by Sumit Kumar · StickForYou"; color: "#7892a4"; font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.margins: 20; spacing: 15
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout { Layout.fillWidth: true
                    Text { text: page.section; color: "#f6fbff"; font.pixelSize: 29; font.bold: true }
                    Text { text: bridge.view.cafeName || "Game Cafe Console"; color: "#8daabd" }
                }
                Item { Layout.fillWidth: true }
                Text { text: bridge.busy ? "Working…" : "● Live"; color: "#35c7c7"; font.bold: true }
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
                            Text { text: modelData.label; color: "#8ca7ba"; font.pixelSize: 13 }
                            Text { text: modelData.value; color: "#f4fafc"; font.pixelSize: 25; font.bold: true }
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
                            Text { text: "ALL COMPUTERS"; color: "#aac3d0"; font.bold: true; font.pixelSize: 12; Layout.fillWidth: true }
                            Text { text: "SORT BY"; color: "#7892a4"; font.bold: true; font.pixelSize: 10 }
                            ComboBox {
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
                                color: rowData.unlockRequested ? "#354238" : bridge.selectedPcId === rowData.pcId ? "#203a4a" : "#1b2b3c"
                                Behavior on implicitHeight { NumberAnimation { duration: 170; easing.type: Easing.OutCubic } }
                                MouseArea { anchors.fill: parent; onClicked: bridge.selectPc(card.rowData.pcId) }
                                ColumnLayout {
                                    id: detailColumn
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                    anchors.margins: 12; spacing: 9
                                    RowLayout {
                                        Layout.fillWidth: true
                                        ColumnLayout { Layout.fillWidth: true
                                            Text { text: card.rowData.name; color: "#f7fbfd"; font.pixelSize: 17; font.bold: true }
                                            Text { text: card.rowData.role + "  ·  " + (card.rowData.online ? "ONLINE" : "OFFLINE") + "  ·  " + card.rowData.access; color: card.rowData.online ? "#51d9bc" : "#91a3b3"; font.pixelSize: 11 }
                                        }
                                        Item { Layout.fillWidth: true }
                                        Text { text: card.rowData.timeText; color: "#35c7c7"; font.pixelSize: 19; font.bold: true }
                                    }
                                    Text { text: card.rowData.player + "  ·  " + (card.rowData.kind === "none" ? "No session" : card.rowData.kind) + "  ·  " + card.rowData.detail; color: "#a8bfce"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                    ColumnLayout {
                                        visible: bridge.expandedPcId === card.rowData.pcId
                                        Layout.fillWidth: true; spacing: 9
                                        Text { text: "PLAYER  " + card.rowData.player + "    ·    " + card.rowData.phase; color: "#f1f7f9"; font.pixelSize: 12 }
                                        Text { text: "Started " + card.rowData.startedText; color: "#91a7ba"; visible: card.rowData.startedText !== ""; font.pixelSize: 12 }
                                        RowLayout {
                                            visible: card.rowData.start
                                            Text { text: "Initial player: Guest"; color: "#a9c0cf"; font.pixelSize: 12 }
                                        }
                                        RowLayout {
                                            visible: card.rowData.start
                                            Text { text: "Session"; color: "#a9c0cf" }
                                            ComboBox { id: kindBox; model: ["Timed", "No timer"]; Layout.preferredWidth: 110 }
                                            ComboBox { id: paidField; model: ["15", "30", "60", "120", "Custom Minutes"]; currentIndex: 2; visible: kindBox.currentIndex === 0; Layout.preferredWidth: 145 }
                                            TextField { id: customPaid; visible: kindBox.currentIndex === 0 && paidField.currentIndex === 4; placeholderText: "Minutes"; Layout.preferredWidth: 88; inputMethodHints: Qt.ImhDigitsOnly }
                                            TextField { id: bufferField; text: "0"; placeholderText: "Buffer"; Layout.preferredWidth: 82; inputMethodHints: Qt.ImhDigitsOnly }
                                            ActionButton { text: "Start session"; onClicked: bridge.startSession(card.rowData.pcId, kindBox.currentIndex === 0 ? "timed" : "open", paidField.currentIndex === 4 ? customPaid.text : paidField.currentText, bufferField.text) }
                                        }
                                        RowLayout {
                                            visible: card.rowData.add
                                            ComboBox { id: addBox; model: ["1","2","5","15","30","60","Custom Minutes"]; currentIndex: 3; Layout.preferredWidth: 145 }
                                            TextField { id: customAdd; visible: addBox.currentIndex === 6; placeholderText: "Minutes"; Layout.preferredWidth: 88; inputMethodHints: Qt.ImhDigitsOnly }
                                            ActionButton {
                                                text: "Add time"
                                                onClicked: {
                                                    var minutes = Number(addBox.currentIndex === 6 ? customAdd.text : addBox.currentText)
                                                    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
                                                        root.ask("Invalid time", "Use a whole number from 1 to 1440 minutes.", "OK", function(){})
                                                        return
                                                    }
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
                                            Text { text: "Unlock requested"; color: "#f1bb70"; visible: card.rowData.unlockRequested }
                                        }
                                        ActionButton { text: "Exit Software"; danger: true; visible: card.rowData.exitSoftware; onClicked: {
                                            var pcId = card.rowData.pcId
                                            root.ask("Exit software on " + card.rowData.name,
                                                "Game Cafe Console will close on this PC" + (card.rowData.end ? " and its active session will end" : "") + ". The Windows desktop may then be accessible without cafe enforcement. Continue?",
                                                "Continue", function(){
                                                    if (card.rowData.end) {
                                                        root.ask("Final confirmation", "End the session on " + card.rowData.name + " and exit Game Cafe Console?", "Exit Software", function(){ bridge.exitRemoteSoftware(pcId) })
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
                        Text { text: "RECENT SESSIONS"; color: "#aac3d0"; font.bold: true; font.pixelSize: 12; Layout.fillWidth: true }
                        ListView { id: historySide; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: bridge.dashboardHistoryModel; spacing: 8
                            Text {
                                objectName: "recentHistoryEmptyState"
                                anchors.centerIn: parent
                                width: parent.width - 16
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                                text: bridge.selectedPcId ? "No sessions recorded for this PC." : "Select a computer to view recent sessions."
                                color: "#718ca0"
                                visible: historySide.count === 0
                                font.pixelSize: 12
                            }
                            delegate: Rectangle { required property var rowData; width: historySide.width; height: 84; radius: 9; color: "#223448"
                                Column { anchors.fill: parent; anchors.margins: 9; spacing: 3
                                    Text { text: rowData.pcName; color: "#f2f8fa"; font.bold: true }
                                    Text { text: rowData.player; color: "#96acbd"; font.pixelSize: 12 }
                                    Text { text: rowData.duration; color: "#35c7c7"; font.pixelSize: 12; font.bold: true }
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
                Panel { Layout.fillWidth: true; implicitHeight: 72; color: "#203a4a"
                    Column { anchors.fill: parent; anchors.margins: 14; spacing: 5
                        Text { text: "PENDING JOIN REQUESTS"; color: "#f4fbff"; font.bold: true; font.pixelSize: 17 }
                        Text { text: (bridge.view.pendingCount || 0) + " awaiting approval"; color: "#35c7c7" }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: 70; visible: (bridge.view.pendingCount || 0) === 0; color: "#1b2b3c"
                    Text { anchors.centerIn: parent; text: "No pending join requests"; color: "#91a9ba" }
                }
                Repeater { model: bridge.joinModel
                    Panel { required property var rowData; Layout.fillWidth: true; implicitHeight: 110
                        RowLayout { anchors.fill: parent; anchors.margins: 15
                            ColumnLayout { Layout.fillWidth: true
                                Text { text: rowData.name; color: "#f4fbff"; font.bold: true }
                                Text { text: rowData.ip + " · " + rowData.pcId; color: "#91a9ba"; font.pixelSize: 11 }
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
                Panel { Layout.fillWidth: true; implicitHeight: 72; color: "#203a4a"
                    Column { anchors.fill: parent; anchors.margins: 14; spacing: 5
                        Text { text: "REGISTERED COMPUTERS"; color: "#f4fbff"; font.bold: true; font.pixelSize: 17 }
                        Text { text: (bridge.view.totalCount || 0) + " known devices"; color: "#35c7c7" }
                    }
                }
                Repeater { model: bridge.memberModel
                    Panel { required property var rowData; Layout.fillWidth: true; implicitHeight: 72
                        RowLayout { anchors.fill: parent; anchors.margins: 12
                            Text { text: rowData.online ? "●" : "○"; color: rowData.online ? "#35c7c7" : "#91a9ba" }
                            Text { text: rowData.name; color: "#f4fbff"; Layout.fillWidth: true }
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
            RowLayout { Text { text: "Session history"; color: "#f4fbff"; font.pixelSize: 19; Layout.fillWidth: true }
                ActionButton { objectName: "allHistoryButton"; text: "All PCs"; secondary: true; onClicked: bridge.showAllHistory() }
            }
            ListView { id: fullHistory; Layout.fillWidth: true; Layout.fillHeight: true; model: bridge.historyModel; clip: true; spacing: 7
                Text { anchors.centerIn: parent; text: "No completed sessions yet"; color: "#718ca0"; visible: fullHistory.count === 0 }
                delegate: Rectangle { required property var rowData; width: fullHistory.width; height: 72; radius: 8; color: "#223448"
                    RowLayout { anchors.fill: parent; anchors.margins: 12
                        ColumnLayout { Layout.fillWidth: true
                            Text { text: rowData.pcName + "  ·  " + rowData.player; color: "#f3fafc"; font.bold: true }
                            Text { text: rowData.started + " → " + rowData.ended; color: "#91a9ba"; font.pixelSize: 11 }
                        }
                        Text { text: rowData.kind + " · " + rowData.duration + " · " + (rowData.kind === "timed" ? rowData.paidMinutes + " paid min · " : "") + rowData.reason; color: "#35c7c7" }
                    }
                }
            }
        } }
    }
    Component { id: settings
        ScrollView { contentWidth: availableWidth
            ColumnLayout { width: Math.min(parent.width, 700); spacing: 15
                Panel { Layout.fillWidth: true; implicitHeight: identity.implicitHeight + 34
                    ColumnLayout { id: identity; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Cafe identity"; color: "#f4fbff"; font.pixelSize: 19; font.bold: true }
                        TextField { id: cafeName; text: bridge.view.settings ? bridge.view.settings.cafeName : ""; placeholderText: "Cafe name"; Layout.fillWidth: true }
                        TextField { id: adminName; text: bridge.view.settings ? bridge.view.settings.adminName : ""; placeholderText: "Admin name"; Layout.fillWidth: true }
                        RowLayout { TextField { id: grace; text: bridge.view.settings ? String(bridge.view.settings.graceMinutes) : "10"; placeholderText: "Grace min"; Layout.fillWidth: true }
                            TextField { id: signout; text: bridge.view.settings ? String(bridge.view.settings.signoutMinutes) : "0"; placeholderText: "Auto signout min"; Layout.fillWidth: true }
                        }
                        Text { text: "Automatic Windows sign-out is disabled pending recovery design."; color: "#91a9ba"; font.pixelSize: 11 }
                        ActionButton { text: "Save settings"; onClicked: bridge.saveSettings(cafeName.text,adminName.text,grace.text,signout.text) }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: avatarSettings.implicitHeight + 34
                    ColumnLayout { id: avatarSettings; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Cafe avatar"; color: "#f4fbff"; font.pixelSize: 19; font.bold: true }
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
                                Text { text: "PNG or JPG · up to 5 MB"; color: "#91a9ba" }
                                Text { text: "Preview fit"; color: "#91a9ba"; font.pixelSize: 12 }
                                ComboBox { id: avatarFit; objectName: "avatarPreviewFit"; model: ["Fit", "Fill"] }
                            }
                        }
                        TextField { id: avatarPath; placeholderText: "Image file path (or drop file here)"; Layout.fillWidth: true }
                        RowLayout { ActionButton { text: "Browse / Choose Image"; secondary: true; onClicked: { var selected = bridge.browseAvatar(); if (selected) avatarPath.text = selected } }
                            ActionButton { text: "Save avatar"; onClicked: bridge.setAvatar(avatarPath.text) }
                            ActionButton { text: "Remove"; secondary: true; onClicked: bridge.removeAvatar() }
                        }
                        DropArea { Layout.fillWidth: true; Layout.preferredHeight: 48
                            onDropped: function(drop) { if (drop.hasUrls && drop.urls.length) avatarPath.text = drop.urls[0] }
                            Rectangle { anchors.fill: parent; radius: 8; color: "#263a4b"; border.color: "#3c5264"
                                Text { anchors.centerIn: parent; text: "Drop a PNG or JPG here"; color: "#91a9ba" }
                            }
                        }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: security.implicitHeight + 34
                    ColumnLayout { id: security; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Admin security"; color: "#f4fbff"; font.pixelSize: 19; font.bold: true }
                        TextField { id: oldPassword; placeholderText: "Current password"; echoMode: TextInput.Password; Layout.fillWidth: true }
                        TextField { id: newPassword; placeholderText: "New password"; echoMode: TextInput.Password; Layout.fillWidth: true }
                        TextField { id: confirmPassword; placeholderText: "Confirm new password"; echoMode: TextInput.Password; Layout.fillWidth: true }
                        ActionButton { text: "Change password"; onClicked: bridge.changePassword(oldPassword.text,newPassword.text,confirmPassword.text) }
                    }
                }
                Panel { Layout.fillWidth: true; implicitHeight: move.implicitHeight + 34
                    ColumnLayout { id: move; anchors.fill: parent; anchors.margins: 17; spacing: 10
                        Text { text: "Change cafe"; color: "#f4fbff"; font.pixelSize: 19; font.bold: true }
                        Text { text: "Discover and request to join another cafe. Your old cafe remains until approval."; color: "#91a9ba"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
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
            Avatar { diameter: 70 }
            Text { text: "Game Cafe Console"; color: "#f4fbff"; font.pixelSize: 25; font.bold: true }
            Text { text: "A local-first gaming cafe management console."; color: "#91a9ba" }
            Text { text: "Developed by " + bridge.view.developer + " · " + bridge.view.brand; color: "#f1f8fa" }
            Text { text: bridge.view.website; color: "#35c7c7"; MouseArea { anchors.fill: parent; onClicked: Qt.openUrlExternally(bridge.view.website) } }
            Text { text: bridge.view.email; color: "#35c7c7"; MouseArea { anchors.fill: parent; onClicked: Qt.openUrlExternally("mailto:" + bridge.view.email) } }
            Item { Layout.fillHeight: true }
        } }
    }
}
