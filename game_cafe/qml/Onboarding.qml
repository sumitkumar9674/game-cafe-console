import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    property string tab: bridge.mode === "candidate" ? "admin" : "discover"
    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        ColumnLayout {
            width: Math.min(parent.width - 48, 780)
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 18
            Item { Layout.preferredHeight: 25 }
            Avatar { diameter: 74; Layout.alignment: Qt.AlignHCenter }
            Text { text: "WELCOME TO GAME CAFE CONSOLE"; color: "#f5fbff"; font.pixelSize: 26; font.bold: true; Layout.alignment: Qt.AlignHCenter }
            Text { text: bridge.statusText; color: "#91a7ba"; Layout.alignment: Qt.AlignHCenter }
            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                ActionButton { text: "Discover"; secondary: page.tab !== "discover"; visible: bridge.mode !== "candidate"; onClicked: page.tab = "discover" }
                ActionButton { text: "Create cafe"; secondary: page.tab !== "create"; visible: bridge.mode !== "candidate"; onClicked: page.tab = "create" }
                ActionButton { text: "Admin access"; secondary: page.tab !== "admin"; visible: bridge.mode === "candidate"; onClicked: page.tab = "admin" }
            }
            Panel {
                Layout.fillWidth: true
                implicitHeight: form.implicitHeight + 42
                ColumnLayout {
                    id: form
                    anchors.fill: parent
                    anchors.margins: 21
                    spacing: 12
                    Text { text: page.tab === "create" ? "Create a local cafe" : page.tab === "admin" ? "Claim Admin role" : "Nearby cafes"; color: "#f3f9fb"; font.pixelSize: 20; font.bold: true }
                    Text { text: "Your cafe runs on your local network."; color: "#91a7ba"; visible: page.tab === "create" }
                    TextField { id: cafe; placeholderText: "Cafe name"; Layout.fillWidth: true; visible: page.tab === "create" }
                    TextField { id: pc; placeholderText: "This PC name"; Layout.fillWidth: true; visible: page.tab === "create" }
                    TextField { id: admin; placeholderText: "Admin name"; Layout.fillWidth: true; visible: page.tab === "create" }
                    TextField { id: password; placeholderText: "Admin password"; echoMode: TextInput.Password; Layout.fillWidth: true; visible: page.tab !== "discover" }
                    TextField { id: confirmation; placeholderText: "Confirm password"; echoMode: TextInput.Password; Layout.fillWidth: true; visible: page.tab === "create" }
                    ActionButton { text: "Create and open Admin Dashboard"; visible: page.tab === "create"; onClicked: bridge.createCafe(cafe.text, pc.text, admin.text, password.text, confirmation.text) }
                    ActionButton { text: "Become Admin"; visible: page.tab === "admin"; onClicked: bridge.claimAdmin(password.text) }
                    ActionButton { text: "Continue as User"; secondary: true; visible: bridge.mode === "candidate"; onClicked: bridge.stayUser() }
                    ActionButton { text: "Search LAN"; secondary: true; visible: page.tab === "discover"; onClicked: bridge.searchPools() }
                    Repeater {
                        model: bridge.view.pools || []
                        Panel {
                            Layout.fillWidth: true
                            implicitHeight: 86
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 12
                                ColumnLayout { Layout.fillWidth: true
                                    Text { text: modelData.cafeName; color: "#f5fbff"; font.bold: true }
                                    Text { text: modelData.ip; color: "#91a7ba" }
                                }
                                TextField { id: joinName; placeholderText: "Your PC name"; Layout.preferredWidth: 170 }
                                ActionButton { text: "Request to join"; onClicked: bridge.startJoin(modelData.poolId, joinName.text) }
                            }
                        }
                    }
                    Text { text: "No cafes found yet. Search again or create one."; color: "#91a7ba"; visible: page.tab === "discover" && (!bridge.view.pools || bridge.view.pools.length === 0) }
                }
            }
            Panel {
                Layout.fillWidth: true
                visible: bridge.mode === "joining"
                implicitHeight: joinInfo.implicitHeight + 40
                ColumnLayout {
                    id: joinInfo; anchors.fill: parent; anchors.margins: 20; spacing: 12
                    Text { text: "Pairing with " + (bridge.view.pairingCafe || "cafe"); color: "#f5fbff"; font.pixelSize: 20 }
                    Text { text: bridge.view.pairingCode || "Waiting for code"; color: "#35c7c7"; font.pixelSize: 30; font.bold: true }
                    Text { text: bridge.view.pairingState || ""; color: "#a9bfd0"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    RowLayout { ActionButton { text: "New code"; onClicked: bridge.renewJoin() } ActionButton { text: "Cancel"; secondary: true; onClicked: bridge.cancelJoin() } }
                }
            }
            Text { text: "Developed by Sumit Kumar · StickForYou"; color: "#7892a4"; font.pixelSize: 11; Layout.alignment: Qt.AlignHCenter }
        }
    }
}
