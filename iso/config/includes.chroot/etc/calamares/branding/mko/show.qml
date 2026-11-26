import QtQuick 2.0;
import calamares.slideshow 1.0;

Presentation {
    id: presentation
    function onActivate() { }
    function onLeave() { }

    Timer {
        interval: 9000
        running: presentation.activatedInCalamares
        repeat: true
        onTriggered: presentation.goToNextSlide()
    }

    Slide {
        Rectangle { anchors.fill: parent; color: "#16202c" }
        Column {
            anchors.centerIn: parent; spacing: 16
            Image { source: "logo.png"; width: 128; height: 128; anchors.horizontalCenter: parent.horizontalCenter }
            Text { text: "MKO OS wird installiert …"; color: "white"; font.pixelSize: 26; anchors.horizontalCenter: parent.horizontalCenter }
            Text { text: "Danach führt dich das Willkommensfenster durch die Einrichtung."; color: "#9fb0c3"; font.pixelSize: 16; anchors.horizontalCenter: parent.horizontalCenter }
        }
    }
    Slide {
        Rectangle { anchors.fill: parent; color: "#16202c" }
        Column {
            anchors.centerIn: parent; spacing: 12
            Text { text: "Super+C – Claude als Seitenleiste"; color: "white"; font.pixelSize: 22 }
            Text { text: "Alt+Leertaste – Suche in Dateien, Paperless und Fotos"; color: "white"; font.pixelSize: 22 }
            Text { text: "Super+Umschalt+H – Haus-Anzeige ein/aus"; color: "white"; font.pixelSize: 22 }
        }
    }
}
