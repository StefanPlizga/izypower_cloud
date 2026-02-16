# Intégration Izypower Cloud pour Home Assistant

[English version](README.en.md)

Cette intégration personnalisée découvre automatiquement toutes les centrales photovoltaïques Izypower Cloud et fournit une surveillance complète de votre installation solaire.

## Installation

### Via HACS (Recommandé)

1. Assurez-vous que [HACS](https://hacs.xyz/) est installé dans votre instance Home Assistant
2. Cliquez sur le bouton ci-dessous pour ajouter ce dépôt à HACS :

   [![Ouvrir votre instance Home Assistant et ouvrir un dépôt dans le magasin communautaire Home Assistant.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=StefanPlizga&repository=izypower_cloud&category=integration)

   Ou manuellement :
   - Dans HACS, cliquez sur "Intégrations"
   - Cliquez sur le menu (trois points) en haut à droite et sélectionnez "Dépôts personnalisés"
   - Ajoutez `https://github.com/StefanPlizga/izypower_cloud` comme dépôt avec la catégorie "Intégration"

3. Recherchez "Izypower Cloud" dans HACS et cliquez sur "Télécharger"
4. Redémarrez Home Assistant
5. Cliquez sur le bouton ci-dessous pour ajouter l'intégration :

   [![Ouvrir votre instance Home Assistant et démarrer la configuration d'une nouvelle intégration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=izypower_cloud)

   Ou manuellement :
   - Allez dans Paramètres > Appareils et services > Ajouter une intégration
   - Recherchez "Izypower Cloud" et suivez les étapes de configuration

### Installation Manuelle

1. Téléchargez la dernière version depuis [GitHub](https://github.com/StefanPlizga/izypower_cloud)
2. Extrayez le contenu et copiez le dossier `custom_components/izypower_cloud` vers le répertoire `custom_components` de votre configuration Home Assistant
3. Si le dossier `custom_components` n'existe pas, créez-le à la racine de votre configuration Home Assistant
4. Redémarrez Home Assistant
5. Allez dans Paramètres > Appareils et services > Ajouter une intégration
6. Recherchez "Izypower Cloud" et suivez les étapes de configuration

## Configuration

- Ajoutez l'intégration via l'interface utilisateur de Home Assistant
- Entrez votre `nom d'utilisateur` et `mot de passe` Izypower Cloud
- Optionnel : Définissez la `période de rafraîchissement` en minutes (par défaut : 3 minutes)
- Après la configuration, vous pouvez modifier la `période de rafraîchissement` depuis le menu Options de l'intégration

> **Note** : La période de rafraîchissement par défaut est de 3 minutes car les données proviennent du cloud Izypower et sont mises à jour dans le cloud toutes les 3 minutes. Il n'est donc pas nécessaire de rafraîchir plus fréquemment. Les données ne sont pas en temps réel, tout comme dans l'application Izypower Cloud.

## Fonctionnalités

### Découverte Automatique
- Toutes les centrales photovoltaïques de votre compte Izypower Cloud sont automatiquement découvertes
- Chaque centrale est créée en tant qu'appareil avec tous les capteurs associés
- Des sous-appareils sont créés pour les onduleurs et autres équipements

### Capteurs de Centrale (Par Centrale)

**Capteurs de Puissance** (W) :
- Puissance Production PV
- Puissance Réseau
- Puissance Consommation
- Puissance Batterie
- Puissance PV Batterie

**Capteurs de Batterie de la Centrale** :
- État de Charge Batterie (%)
- Dernière Mise à Jour (horodatage)

**Capteurs d'Énergie** (kWh) :
- Production : Jour, Mois, Année, Total
- Import Réseau : Jour, Mois, Année, Total
- Export Réseau : Jour, Mois, Année, Total
- Consommation : Jour, Mois, Année, Total
- Consommation depuis PV : Jour, Mois, Année, Total (calculé)
- Charge Batterie : Jour, Mois, Année, Total
- Décharge Batterie : Jour, Mois, Année, Total

**Capteurs de Taux** (%) pour les périodes Jour, Mois, Année et Total :
- Taux de Couverture
- Taux de Charge Batterie
- Taux d'Autoconsommation
- Taux d'Export Réseau
- Taux de Décharge Batterie
- Taux de Consommation depuis PV
- Taux d'Import Réseau

**Informations de l'Appareil** :
- Capacité Installée (W)

### Capteurs d'Appareil (Par Onduleur/Équipement)

**Connectivité** :
- État en Ligne
- Force du Signal Wi-Fi (RSSI en dBm)
- Nom du Réseau Wi-Fi
- Adresse IP

**Production PV** :
- Puissance individuelle par chaîne PV (PV1, PV2, etc.) en Watts

**Capteurs Spécifiques** (selon le type d'appareil) :
- État de Charge Moyen (%) - pour les appareils avec batterie intégrée
- Mode Cluster - pour les appareils en configuration multi-onduleur (Maître/Esclave/Autonome)

### Capteurs d'Appareil Batterie (Par Batterie avec Modules)

Pour les batteries avec modules/liens individuels, des sous-appareils supplémentaires sont créés :

**Appareil Batterie Parent** :
- État de Charge (%)
- Énergie (kWh)

**Sous-Appareils de Lien de Batterie** (par module de batterie individuel) :
- État de Charge (%)
- Énergie (kWh)

### Fonctionnalités Techniques

- Polling Cloud : Récupération des données via API Izypower Cloud
- Configuration via config flow et options flow Home Assistant
- Période de rafraîchissement personnalisable
- Découverte automatique des centrales et équipements
- Code propriétaire : @StefanPlizga

### Documentation et Support

- [Documentation officielle](https://github.com/StefanPlizga/izypower_cloud/blob/main/README.md)
- [Suivi des issues](https://github.com/StefanPlizga/izypower_cloud/issues)

### Fonctionnalités Techniques

- **Rafraîchissement automatique du token** : Les jetons d'authentification sont gérés automatiquement
- **Logique de nouvelle tentative robuste** : Les erreurs réseau sont gérées avec backoff exponentiel et gigue
- **Mises à jour en temps réel** : Toutes les données sont rafraîchies à l'intervalle configuré
- **Validation des identifiants** : Validation à la configuration avec flux de réauthentification automatique si nécessaire
- **Notifications persistantes** : Alertes si les identifiants expirent ou deviennent invalides
- **Support multilingue** : Traductions en anglais et français incluses

## Organisation des Appareils

- **Appareil Centrale** : Appareil principal contenant les capteurs au niveau de la centrale (puissance, énergie, taux, capacité, état de charge batterie, dernière mise à jour)
- **Sous-appareils Onduleur/Équipement** : Chaque onduleur/équipement sous la centrale avec des capteurs spécifiques (état en ligne, Wi-Fi, chaînes PV, état de charge moyen, mode cluster)
- **Sous-appareils Batterie** : Pour les batteries avec modules, un appareil batterie parent avec capteurs d'énergie et d'état de charge
- **Sous-appareils Link de Batterie** : Pour chaque module Link de batterie, un sous-appareil avec son propre état de charge et énergie
- **Regroupement logique** : Tous les capteurs sont correctement catégorisés avec les classes d'appareil et d'état appropriées pour la compatibilité avec le tableau de bord Énergie de Home Assistant

## Rafraîchissement des Données

- Intervalle de rafraîchissement par défaut : **3 minutes**
- Tous les capteurs se mettent à jour simultanément à chaque cycle de rafraîchissement
- Le coordinateur récupère :
  - Liste et informations des centrales
  - Données de puissance en temps réel
  - État de charge de la batterie de la centrale et horodatage
  - Statistiques énergétiques (quotidiennes, mensuelles, annuelles, totales)
  - Pourcentages de taux
  - État des appareils et informations Wi-Fi
  - Production individuelle des chaînes PV
  - Données de batterie

## Remarques

- Tous les capteurs d'énergie sont compatibles avec le tableau de bord Énergie de Home Assistant
- Les capteurs de taux analysent automatiquement les valeurs de pourcentage de l'API
- Les capteurs calculés (comme Consommation depuis PV) garantissent des valeurs non négatives
- Les informations Wi-Fi ne sont disponibles que pour les appareils avec numéros de série
- Le capteur d'état de charge moyen n'apparaît que pour les appareils avec batterie intégrée
- Le capteur de mode cluster n'apparaît que pour les appareils en configuration multi-onduleur
- Les sous-appareils de batterie sont créés automatiquement pour les batteries avec modules Link
- Si les identifiants expirent, une notification persistante invitera à la réauthentification
