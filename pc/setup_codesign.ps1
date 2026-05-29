# setup_codesign.ps1 — Crée et installe le certificat de signature MeetNote.
# À exécuter UNE SEULE FOIS par poste (ou après réinstallation Windows).
# Lancer en tant qu'utilisateur normal (pas besoin d'admin).

$ErrorActionPreference = "Stop"

# Vérifier si un cert valide existe déjà
$existing = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object { $_.Subject -like '*MeetNote*' -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) } |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1

if ($existing) {
    Write-Host "Certificat MeetNote déjà présent : $($existing.Thumbprint) (expire $($existing.NotAfter.ToString('dd/MM/yyyy')))"
    $cert = $existing
} else {
    Write-Host "Création du certificat MeetNote..."
    $cert = New-SelfSignedCertificate `
        -Subject "CN=MeetNote, O=HopitalRothschild, L=Paris, C=FR" `
        -Type CodeSigningCert `
        -KeyUsage DigitalSignature `
        -FriendlyName "MeetNote Code Signing" `
        -NotAfter (Get-Date).AddYears(10) `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -HashAlgorithm SHA256
    Write-Host "Certificat créé : $($cert.Thumbprint)"
}

# Installer dans Trusted Root (confiance locale)
$rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "CurrentUser")
$rootStore.Open("ReadWrite")
$rootStore.Add($cert)
$rootStore.Close()
Write-Host "Installé dans Trusted Root CA"

# Installer dans Trusted Publishers (SmartScreen + EDR)
$pubStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPublisher", "CurrentUser")
$pubStore.Open("ReadWrite")
$pubStore.Add($cert)
$pubStore.Close()
Write-Host "Installé dans Trusted Publishers"

Write-Host ""
Write-Host "=== DONE ==="
Write-Host "Thumbprint : $($cert.Thumbprint)"
Write-Host "Expire     : $($cert.NotAfter.ToString('dd/MM/yyyy'))"
Write-Host ""
Write-Host "Le prochain build_spec.py signera automatiquement MeetNote.exe avec ce certificat."
