SELECT k.nom, c.prix * c.quantite AS total
FROM commande c
JOIN client k ON c.client_id = k.id;
