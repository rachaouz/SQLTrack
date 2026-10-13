SELECT id, couleur, k.inconnue
FROM client k
JOIN commande c ON c.client_id = k.id;
