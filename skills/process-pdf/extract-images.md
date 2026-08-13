# Extract Images (pypdf)

Examples for extracting embedded images from PDFs using `pypdf`.


In order to use the following code you need to install dependencies, such as `Pillow`

```
pip install pypdf[image]
```

For JBIG2 support, you need to install a global OS-level package as well: jbig2dec The installation procedure depends on your operating system

Every page of a PDF document can contain an arbitrary number of images.
The names of the files may not be unique.

Assumes the input PDF exists on disk.

```python
from pypdf import PdfReader

reader = PdfReader("example.pdf")

page = reader.pages[0]

for i, image_file_object in enumerate(page.images):
    file_name = "out-image-" + str(i) + "-" + image_file_object.name
    image_file_object.image.save(file_name)
```

# Other images

Some other objects can contain images, such as stamp annotations.

You can extract the image from the annotation with the following code:

```python
from pypdf import PdfReader

reader = PdfReader("example.pdf")
im = (
    reader.pages[0]["/Annots"][4]["/Parent"]
    .get_object()["/AP"]["/N"]["/Resources"]["/XObject"]["/Im4"]
    .decode_as_image()
)

im.save("out-annotation-image.png")
```
