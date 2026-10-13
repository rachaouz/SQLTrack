WITH ventes AS (
    SELECT client_id, prix * quantite AS total
    FROM commande
)
SELECT k.nom, SUM(v.total) AS chiffre_affaires
FROM ventes v
JOIN client k ON k.id = v.client_id
GROUP BY k.nom;
