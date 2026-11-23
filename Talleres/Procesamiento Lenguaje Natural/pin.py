palabras_positivas = [
	"feliz", "alegre", "contento", "excelente", "increíble", "genial",
	"encantó", "encanta", "bueno", "buena", "maravilloso", "divertido",
	"divertida", "amo", "amé", "recomiendo", "espectacular", "bien"
]

palabras_negativas = [
    "triste", "mal", "malo", "mala", "pésimo", "horrible", "aburrido",
    "aburrida", "desepcionante", "odio", "odié", "feo", "fea",
    "terrible", "deprimido", "deprimida", "enojado", "enojada"
]

conectores_de_contraste = ["pero", "aunque", "sin_embargo"]

def tokenizar(texto):
    texto = texto.lower()
    texto = texto.replace("sin embargo", "sin_embargo")
    for signo in [".", ",", "!", "?", "¿", "¡"]:
        texto = texto.replace(signo, "")
    return texto.split()

def analizar_sentimiento(texto):
    tokens = tokenizar(texto)

    puntaje = 0
    encontro_conector = False

    for palabra in tokens:
        if palabra in conectores_de_contraste:
            encontro_conector = True
            continue

        if palabra in palabras_positivas:
            puntaje += 2 if encontro_conector else 1
        elif palabra in palabras_negativas:
            puntaje -= 2 if encontro_conector else 1

    if puntaje > 0:
        return "Positivo", puntaje
    elif puntaje < 0:
        return "Negativo", puntaje
    else:
        return "Neutral", puntaje
	
if __name__ == "__main__":
	print("Análisis de sentimiento")
	print("Escribe una frase y te dire si es feliz, triste o neutral")
	print("(Escribe 'salir' para terminar)\n")

	while True:
		texto_usuario = input("Escribe tu frase: ")
		if texto_usuario.lower() == "salir":
			break

		resultado, puntaje = analizar_sentimiento(texto_usuario)
		print(f"Sentimiento: {resultado} (Puntaje: {puntaje})\n")
			