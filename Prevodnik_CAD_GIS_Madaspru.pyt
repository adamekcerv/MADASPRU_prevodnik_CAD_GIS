# -*- coding: utf-8 -*-
import arcpy
import os

def parameter(displayName, name, datatype,
              parameterType='Required',
              direction='Input',
              multiValue=False,
              defaultValue=None):
    """
    Pomocná funkce pro tvorbu parametrů v Python Toolboxu.
    """
    param = arcpy.Parameter(
        displayName=displayName,
        name=name,
        datatype=datatype,
        parameterType=parameterType,
        direction=direction,
        multiValue=multiValue
    )
    if defaultValue is not None:
        param.value = defaultValue
    return param

def get_all_fc_names(gdb_path):
    """
    Projde celou geodatabázi (včetně feature datasetů) a vrátí množinu všech existujících feature class názvů.
    """
    fc_names = set()
    walk = arcpy.da.Walk(gdb_path, datatype="FeatureClass")
    for dirpath, dirnames, filenames in walk:
        for fc in filenames:
            fc_names.add(fc)
    return fc_names

def generate_unique_fc_name(base, workspace):
    """
    Vygeneruje unikátní název feature class v rámci workspace.
    Pokud již jméno existuje, přidá se přípona _1, _2, atd.
    """
    existing = get_all_fc_names(workspace)
    unique = base
    counter = 1
    while unique in existing:
        unique = f"{base}_{counter}"
        counter += 1
    return unique

# Globální slovník přípon dle typu FC z CADu
GEOMETRY_SUFFIX = {
    "Point": "PT",
    "Polyline": "LN", 
    "Polygon": "PL",
    "Annotation": "ANN",
    "MultiPatch": "MP"
}

class CadLayer(object):
    """
    Reprezentuje jednu vrstvu z CADu a související geometrii.
    """
    def __init__(self, cad_file, cad_fc, cad_layer):
        self.cad_file = cad_file
        self.cad_fc = cad_fc
        self.name = cad_layer

    def geometry_suffix(self):
        return GEOMETRY_SUFFIX.get(self.cad_fc, "OTH")

    def export(self, output_workspace, new_name=None,
               spatial_ref=None, transform_method=None,
               out_prefix=""):
        """
        Exportuje jednu CAD vrstvu do geodatabáze.
        """
        if not new_name:
            base_name = self.name
            suffix = self.geometry_suffix()
            new_name = f"{base_name}_{suffix}"
        if out_prefix:
            new_name = f"{out_prefix}{new_name}"
        
        output_name = arcpy.ValidateTableName(new_name, output_workspace)
        
        # Získání kořenové geodatabáze pro kontrolu jedinečnosti názvů
        desc_ws = arcpy.Describe(output_workspace)
        if desc_ws.datatype == "FeatureDataset":
            gdb_path = os.path.dirname(output_workspace)
        else:
            gdb_path = output_workspace
            
        # Zajištění jedinečnosti názvu
        existing_names = get_all_fc_names(gdb_path)
        unique_name = output_name
        counter = 1
        while unique_name in existing_names:
            unique_name = f"{output_name}_{counter}"
            counter += 1
        output_name = unique_name

        fc_path = os.path.join(self.cad_file, self.cad_fc)
        if not arcpy.Exists(fc_path):
            arcpy.AddWarning(f"Feature class '{fc_path}' neexistuje, vrstva '{self.name}' přeskočena.")
            return None
        
        # SQL dotaz pro filtrování vrstvy
        field_delimited = arcpy.AddFieldDelimiters(fc_path, "Layer")
        sql = f"{field_delimited} = '{self.name}'"
        arcpy.AddMessage(f"[export] Vrstva '{self.name}' => SQL: {sql}")
        
        # Kontrola počtu záznamů
        count = 0
        try:
            with arcpy.da.SearchCursor(fc_path, ["OID@"], sql) as cursor:
                for _ in cursor:
                    count += 1
        except Exception as e:
            arcpy.AddWarning(f"[export] Chyba při čtení záznamů: {e}")
            return None
        
        if count == 0:
            arcpy.AddWarning(f"[export] Vrstva '{self.name}' (geom: {self.cad_fc}) nemá záznamy -> přeskočeno.")
            return None
        
        arcpy.AddMessage(f"[export] Nalezeno {count} záznamů pro '{self.name}' ({self.cad_fc}).")
        
        # Export vrstvy
        out_fc = os.path.join(output_workspace, output_name)
        try:
            arcpy.FeatureClassToFeatureClass_conversion(fc_path, output_workspace, output_name, sql)
            arcpy.AddMessage(f"[export] Vrstva '{self.name}' exportována jako '{output_name}'.")
        except Exception as e:
            arcpy.AddWarning(f"[export] Chyba při exportu '{self.name}': {e}")
            return None

        # Definice a reprojekce souřadnicového systému
        if spatial_ref:
            out_fc = self.define_and_project(out_fc, spatial_ref, transform_method)
            
        return out_fc

    def define_and_project(self, fc, spatial_ref, transform_method=None):
        """
        Definuje souřadnicový systém a provede reprojekci pokud je potřeba.
        """
        try:
            desc = arcpy.Describe(fc)
            source_sr = desc.spatialReference
            
            # Pokud zdrojový SR není definován nebo je neznámý, definujeme ho
            if not source_sr or source_sr.name.lower() == "unknown" or source_sr.factoryCode == 0:
                arcpy.AddMessage(f"[export] Definuji SR: {spatial_ref.name}")
                arcpy.DefineProjection_management(fc, spatial_ref)
                source_sr = spatial_ref

            # Kontrola, zda je potřeba reprojekce
            need_reproject = False
            if (source_sr.factoryCode != 0) and (spatial_ref.factoryCode != 0):
                if source_sr.factoryCode != spatial_ref.factoryCode:
                    need_reproject = True
            else:
                if source_sr.exportToString() != spatial_ref.exportToString():
                    need_reproject = True

            if need_reproject:
                out_ws = os.path.dirname(fc)
                base_name = os.path.basename(fc)
                temp_fc = os.path.join(out_ws, base_name + "_prj")
                
                arcpy.AddMessage(f"[export] Reprojekce '{base_name}' -> {spatial_ref.name}")
                
                if transform_method:
                    arcpy.Project_management(fc, temp_fc, spatial_ref, transform_method, source_sr)
                else:
                    arcpy.Project_management(fc, temp_fc, spatial_ref)
                    
                arcpy.Delete_management(fc)
                arcpy.Rename_management(temp_fc, base_name)
                fc = os.path.join(out_ws, base_name)
                
        except Exception as e:
            arcpy.AddWarning(f"[export] Nelze reprojektovat '{fc}': {e}")
            
        return fc


class CadFile(object):
    """
    Reprezentuje CAD soubor a jeho vrstvy.
    """
    def __init__(self, cad_file):
        self.cad_file = cad_file
        self.display_map = self.get_layers()
        self.layer_display_names = sorted(self.display_map.keys())

    def get_layers(self):
        """
        Načte všechny vrstvy z CAD souboru.
        """
        arcpy.env.workspace = self.cad_file
        geometry_types = ["Point", "Polyline", "Polygon", "Annotation", "MultiPatch"]
        result = {}
        
        for fc_type in geometry_types:
            if arcpy.Exists(fc_type):
                try:
                    with arcpy.da.SearchCursor(fc_type, ["Layer"]) as cur:
                        all_lays = [row[0] for row in cur]
                    for lyr in sorted(set(all_lays)):
                        disp_name = f"{lyr} ({fc_type})"
                        result[disp_name] = CadLayer(self.cad_file, fc_type, lyr)
                except Exception as e:
                    arcpy.AddWarning(f"Chyba při načítání '{fc_type}': {e}")
        
        if not result:
            arcpy.AddWarning("[CadFile] Žádné vrstvy v CADu.")
        else:
            arcpy.AddMessage("[CadFile] Nalezené vrstvy: " + ", ".join(result.keys()))
        
        return result

    def export_layers(self, selected_display_names, output_workspace,
                    spatial_ref=None, transform_method=None, out_prefix=""):
        """
        Exportuje vybrané vrstvy do geodatabáze.
        """
        exported_layers = []
        polylines_for_merge = []  # Seznam pro polyline vrstvy určené k merge
        point_layers_for_join = []  # Seznam pro bodové vrstvy určené k spatial join
        resene_point_fc = None  # Bod Resene_uzemi pro speciální zpracování

        # Pokud nejsou vybrané konkrétní vrstvy, exportujeme všechny
        if not selected_display_names:
            items = self.display_map.items()
        else:
            items = [(dn, self.display_map[dn]) for dn in selected_display_names if dn in self.display_map]

        for disp_name, cad_layer in items:
            # Kontrola, zda se jedná o polyline vrstvy určené pro speciální zpracování
            is_special_polyline = (
                cad_layer.name in ["101110_PL_Resene_uzemi", "200000_PL_Cast_uzemi"] and 
                cad_layer.cad_fc == "Polyline"
            )
            
            # Kontrola, zda se jedná o bodové vrstvy určené pro spatial join
            is_special_point = (
                cad_layer.name in ["202110_BL_Cast_uzemi_UP", "203110_BL_Cast_uzemi_SB", 
                                  "204110_BL_Cast_uzemi_NB", "205110_BL_Cast_uzemi_XB"] and 
                cad_layer.cad_fc == "Point"
            )
            
            # Kontrola pro bod Resene_uzemi (speciální zpracování)
            is_resene_point = (
                cad_layer.name == "101111_BL_Resene_uzemi" and 
                cad_layer.cad_fc == "Point"
            )
            
            if is_special_polyline:
                # Export polyline vrstvy pro pozdější merge
                exported = cad_layer.export(
                    output_workspace,
                    spatial_ref=spatial_ref,
                    transform_method=transform_method,
                    out_prefix=out_prefix
                )
                if exported:
                    polylines_for_merge.append(exported)
                    arcpy.AddMessage(f"[export_layers] Polyline vrstva '{cad_layer.name}' přidána pro merge.")
            elif is_special_point:
                # Export bodové vrstvy pro pozdější spatial join
                exported = cad_layer.export(
                    output_workspace,
                    spatial_ref=spatial_ref,
                    transform_method=transform_method,
                    out_prefix=out_prefix
                )
                if exported:
                    point_layers_for_join.append(exported)
                    arcpy.AddMessage(f"[export_layers] Bodová vrstva '{cad_layer.name}' přidána pro spatial join.")
            elif is_resene_point:
                # Export bodu Resene_uzemi pro speciální zpracování
                exported = cad_layer.export(
                    output_workspace,
                    spatial_ref=spatial_ref,
                    transform_method=transform_method,
                    out_prefix=out_prefix
                )
                if exported:
                    resene_point_fc = exported
                    arcpy.AddMessage(f"[export_layers] Bod Resene_uzemi '{cad_layer.name}' exportován pro speciální zpracování.")
            else:
                # Standardní export ostatních vrstev
                exported = cad_layer.export(
                    output_workspace,
                    spatial_ref=spatial_ref,
                    transform_method=transform_method,
                    out_prefix=out_prefix
                )
                if exported:
                    exported_layers.append(exported)

        # Speciální zpracování polyline vrstev - merge, feature to polygon, integrate
        polygon_fc = None
        if len(polylines_for_merge) >= 1:
            try:
                polygon_fc = self.process_polylines_to_polygon(
                    polylines_for_merge, output_workspace, out_prefix, spatial_ref
                )
                if polygon_fc:
                    exported_layers.append(polygon_fc)
                    # Zachovat obě původní polyline vrstvy
                    for pl_fc in polylines_for_merge:
                        exported_layers.append(pl_fc)
                        arcpy.AddMessage(f"[export_layers] Zachována polyline vrstva: {os.path.basename(pl_fc)}")
            except Exception as e:
                arcpy.AddError(f"[export_layers] Chyba při zpracování polyline vrstev: {e}")
                # V případě chyby ponecháme původní polyline vrstvy
                exported_layers.extend(polylines_for_merge)

        # Spatial join bodových vrstev k polygonům a analýza
        if polygon_fc and len(point_layers_for_join) > 0:
            try:
                analysis_results = self.perform_spatial_join_analysis(
                    polygon_fc, point_layers_for_join, output_workspace, out_prefix
                )
                if analysis_results:
                    exported_layers.extend(analysis_results)
                    
                    # Přichycení původní linie Resene_uzemi k finálnímu polygonu
                    resene_line_fc = None
                    for pl_fc in polylines_for_merge:
                        if "101110_PL_Resene_uzemi" in os.path.basename(pl_fc):
                            resene_line_fc = pl_fc
                            break
                    
                    if resene_line_fc:
                        snapped_fc = self.snap_resene_line_to_polygon(
                            polygon_fc, resene_line_fc, output_workspace, out_prefix
                        )
                        if snapped_fc:
                            # Zpracování bodu Resene_uzemi s přichycenou linií
                            if resene_point_fc:
                                updated_snapped_fc = self.process_resene_point_with_line(
                                    snapped_fc, resene_point_fc, output_workspace, out_prefix
                                )
                                if updated_snapped_fc:
                                    # Polygon s atributem "bod" je výsledek, snapped linie se neukládá
                                    exported_layers.append(updated_snapped_fc)
                                # Ponechat původní bod pro referenci
                                exported_layers.append(resene_point_fc)
                            else:
                                # Pokud není bod, snapped linie se také neukládá
                                pass
                    
                    # Smazat původní polygonovou vrstvu, protože máme už tu s body
                    try:
                        arcpy.Delete_management(polygon_fc)
                        arcpy.AddMessage(f"[export_layers] Smazána původní polygonová vrstva: {os.path.basename(polygon_fc)}")
                    except Exception as e:
                        arcpy.AddWarning(f"[export_layers] Nelze smazat původní polygon: {e}")
                # Ponechat původní bodové vrstvy
                exported_layers.extend(point_layers_for_join)
            except Exception as e:
                arcpy.AddError(f"[export_layers] Chyba při spatial join analýze: {e}")
                # V případě chyby ponecháme původní bodové vrstvy
                exported_layers.extend(point_layers_for_join)
        else:
            # Pokud není polygon nebo bodové vrstvy, přidat bodové vrstvy standardně
            exported_layers.extend(point_layers_for_join)
        
        return exported_layers

    def process_polylines_to_polygon(self, polyline_fcs, output_workspace, out_prefix, spatial_ref):
        """
        Zpracuje polyline vrstvy - merge, feature to polygon, integrate.
        """
        arcpy.AddMessage("[process_polylines_to_polygon] Začínám speciální zpracování polyline vrstev.")
        
        try:
            # Získání kořenové geodatabáze pro kontrolu jedinečnosti názvů
            desc_ws = arcpy.Describe(output_workspace)
            if desc_ws.datatype == "FeatureDataset":
                root_gdb = os.path.dirname(output_workspace)
            else:
                root_gdb = output_workspace
            
            # 1. Merge polyline vrstev
            merged_name = f"{out_prefix}Merged_Polylines" if out_prefix else "Merged_Polylines"
            merged_fc = os.path.join(output_workspace, generate_unique_fc_name(merged_name, root_gdb))
            
            arcpy.AddMessage(f"[process_polylines_to_polygon] 1. Merge polyline vrstev do: {merged_fc}")
            arcpy.management.Merge(polyline_fcs, merged_fc)
            
            # 2. Snap linií k sobě navzájem s tolerancí 30 cm pro spojení neuzavřených konců (CASE I - pouze EDGE)
            arcpy.AddMessage("[process_polylines_to_polygon] 2. Snap linií k hranám (EDGE) - tolerance 30 cm")
            snap_env = [[merged_fc, "EDGE", "0.3 Meters"]]
            arcpy.edit.Snap(merged_fc, snap_env)
            
            # 3. Feature to Polygon - s kontrolou jedinečnosti názvu proti celé geodatabázi
            polygon_name = f"{out_prefix}Resene_uzemi_PL" if out_prefix else "Resene_uzemi_PL"
            unique_polygon_name = generate_unique_fc_name(polygon_name, root_gdb)
            polygon_fc = os.path.join(output_workspace, unique_polygon_name)
            
            arcpy.AddMessage(f"[process_polylines_to_polygon] 3. Feature to Polygon: {polygon_fc}")
            arcpy.management.FeatureToPolygon(
                in_features=merged_fc,
                out_feature_class=polygon_fc,
                cluster_tolerance="",
                attributes="ATTRIBUTES"
            )
            
            # 4. Integrate s tolerancí 30 cm
            arcpy.AddMessage("[process_polylines_to_polygon] 4. Integrate s tolerancí 30 cm")
            arcpy.management.Integrate([polygon_fc], "0.3 Meters")
            
            # Smazat dočasnou merged polyline vrstvu
            arcpy.Delete_management(merged_fc)
            
            arcpy.AddMessage(f"[process_polylines_to_polygon] Úspěšně vytvořena polygonová vrstva: {polygon_fc}")
            return polygon_fc
            
        except Exception as e:
            arcpy.AddError(f"[process_polylines_to_polygon] Chyba při zpracování: {e}")
            return None

    def perform_spatial_join_analysis(self, polygon_fc, point_fcs, output_workspace, out_prefix):
        """
        Provede spatial join bodových vrstev k polygonům a přidá atribut "bod" s hodnocením.
        """
        arcpy.AddMessage("[perform_spatial_join_analysis] Začínám spatial join analýzu.")
        
        try:
            # Získání kořenové geodatabáze pro kontrolu jedinečnosti názvů
            desc_ws = arcpy.Describe(output_workspace)
            if desc_ws.datatype == "FeatureDataset":
                root_gdb = os.path.dirname(output_workspace)
            else:
                root_gdb = output_workspace
            
            # 1. Merge všech bodových vrstev pro analýzu
            merged_points_name = f"{out_prefix}Merged_Points_temp" if out_prefix else "Merged_Points_temp"
            merged_points_fc = os.path.join(output_workspace, generate_unique_fc_name(merged_points_name, root_gdb))
            
            arcpy.AddMessage(f"[perform_spatial_join_analysis] 1. Merge bodových vrstev do: {merged_points_fc}")
            arcpy.management.Merge(point_fcs, merged_points_fc)
            
            # 2. Hlavní Spatial Join s připojením atributů a počítáním
            final_join_name = f"{out_prefix}Resene_uzemi_with_Points" if out_prefix else "Resene_uzemi_with_Points"
            final_join_fc = os.path.join(output_workspace, generate_unique_fc_name(final_join_name, root_gdb))
            
            arcpy.AddMessage(f"[perform_spatial_join_analysis] 2. Hlavní Spatial Join s atributy: {final_join_fc}")
            arcpy.analysis.SpatialJoin(
                target_features=polygon_fc,
                join_features=merged_points_fc,
                out_feature_class=final_join_fc,
                join_operation="JOIN_ONE_TO_ONE",
                join_type="KEEP_ALL",
                match_option="CONTAINS"
            )
            
            # 3. Přidání pole "bod" s hodnocením na základě Join_Count
            arcpy.management.AddField(final_join_fc, "bod", "TEXT", field_length=20)
            
            arcpy.AddMessage("[perform_spatial_join_analysis] 3. Nastavuji hodnocení na základě Join_Count")
            with arcpy.da.UpdateCursor(final_join_fc, ["Join_Count", "bod"]) as cursor:
                for row in cursor:
                    join_count = row[0] if row[0] is not None else 0
                    
                    # Nastavení hodnoty pole "bod" podle skutečného Join_Count
                    if join_count == 0:
                        row[1] = "bez bodu"
                    elif join_count == 1:
                        row[1] = "v pořádku"
                    else:
                        row[1] = "více bodů"
                    
                    cursor.updateRow(row)
            
            # 4. Vyčištění dočasných dat
            arcpy.Delete_management(merged_points_fc)
            
            # 5. Statistiky na základě skutečných Join_Count hodnot
            stats_dict = {"bez bodu": 0, "v pořádku": 0, "více bodů": 0}
            with arcpy.da.SearchCursor(final_join_fc, ["bod"]) as cursor:
                for row in cursor:
                    if row[0] in stats_dict:
                        stats_dict[row[0]] += 1
            
            total_count = sum(stats_dict.values())
            
            arcpy.AddMessage(f"[perform_spatial_join_analysis] Analýza dokončena:")
            arcpy.AddMessage(f"  - Celkem polygonů: {total_count}")
            arcpy.AddMessage(f"  - V pořádku (1 bod): {stats_dict['v pořádku']}")
            arcpy.AddMessage(f"  - Bez bodu: {stats_dict['bez bodu']}")
            arcpy.AddMessage(f"  - Více bodů: {stats_dict['více bodů']}")
            arcpy.AddMessage(f"  - Výsledná vrstva: {os.path.basename(final_join_fc)}")
            
            return [final_join_fc]
            
        except Exception as e:
            arcpy.AddError(f"[perform_spatial_join_analysis] Chyba při analýze: {e}")
            return []

    def snap_resene_line_to_polygon(self, polygon_fc, resene_line_fc, output_workspace, out_prefix):
        """
        Přichytí původní linii Resene_uzemi k hranicím finálního polygonu.
        """
        arcpy.AddMessage("[snap_resene_line_to_polygon] Začínám přichycení linie k polygonu.")
        
        try:
            # Získání kořenové geodatabáze pro kontrolu jedinečnosti názvů
            desc_ws = arcpy.Describe(output_workspace)
            if desc_ws.datatype == "FeatureDataset":
                root_gdb = os.path.dirname(output_workspace)
            else:
                root_gdb = output_workspace
            
            # 1. Převést polygon na linie pro snapping
            temp_polygon_lines = "in_memory\\temp_polygon_lines"
            arcpy.AddMessage(f"[snap_resene_line_to_polygon] 1. Převádím polygon na linie pro snapping")
            arcpy.management.PolygonToLine(polygon_fc, temp_polygon_lines)
            
            # 2. Zkopírovat původní linii pro úpravu
            temp_resene_copy = "in_memory\\temp_resene_copy"
            arcpy.management.CopyFeatures(resene_line_fc, temp_resene_copy)
            
            # 3. Snap původní linie k hranicím polygonu
            arcpy.AddMessage(f"[snap_resene_line_to_polygon] 2. Přichycuji linii k hranicím polygonu (tolerance 1m)")
            snap_env = [[temp_polygon_lines, "EDGE", "1 Meters"]]
            arcpy.edit.Snap(temp_resene_copy, snap_env)
            
            # 4. Vrácení dočasné snapped linie (bez uložení do geodatabáze)
            arcpy.AddMessage(f"[snap_resene_line_to_polygon] 3. Snapped linie připravena pro další zpracování")
            
            # 5. Vyčištění dočasných dat (kromě temp_resene_copy, kterou vracíme)
            arcpy.Delete_management(temp_polygon_lines)
            
            arcpy.AddMessage(f"[snap_resene_line_to_polygon] Úspěšně vytvořena dočasná snapped linie pro zpracování polygonu")
            
            return temp_resene_copy  # Vrácení dočasné linie místo uložené
            
        except Exception as e:
            arcpy.AddError(f"[snap_resene_line_to_polygon] Chyba při snapping: {e}")
            return None

    def process_resene_point_with_line(self, snapped_line_fc, resene_point_fc, output_workspace, out_prefix):
        """
        Zpracuje bod Resene_uzemi s přichycenou linií - vytvoří polygon z linie, spatial join, vrátí polygon s atributem "bod".
        """
        arcpy.AddMessage("[process_resene_point_with_line] Začínám zpracování bodu Resene_uzemi s linií.")
        
        try:
            # Získání kořenové geodatabáze pro kontrolu jedinečnosti názvů
            desc_ws = arcpy.Describe(output_workspace)
            if desc_ws.datatype == "FeatureDataset":
                root_gdb = os.path.dirname(output_workspace)
            else:
                root_gdb = output_workspace
            
            # 1. Vytvoření polygonu z snapped linie pomocí Feature to Polygon
            temp_polygon = "in_memory\\temp_resene_polygon"
            arcpy.AddMessage(f"[process_resene_point_with_line] 1. Vytvářím polygon z snapped linie pomocí Feature to Polygon")
            arcpy.management.FeatureToPolygon(
                in_features=snapped_line_fc,
                out_feature_class=temp_polygon,
                cluster_tolerance="",
                attributes="ATTRIBUTES"
            )
            
            # 2. Spatial Join bodu k polygonu
            temp_join = "in_memory\\temp_resene_join"
            arcpy.AddMessage(f"[process_resene_point_with_line] 2. Spatial join bodu k polygonu")
            arcpy.analysis.SpatialJoin(
                target_features=temp_polygon,
                join_features=resene_point_fc,
                out_feature_class=temp_join,
                join_operation="JOIN_ONE_TO_ONE",
                join_type="KEEP_ALL",
                match_option="CONTAINS"
            )
            
            # 3. Přidání pole "bod" s hodnocením
            arcpy.management.AddField(temp_join, "bod", "TEXT", field_length=20)
            
            arcpy.AddMessage("[process_resene_point_with_line] 3. Nastavuji hodnocení bodu")
            with arcpy.da.UpdateCursor(temp_join, ["Join_Count", "bod"]) as cursor:
                for row in cursor:
                    join_count = row[0] if row[0] is not None else 0
                    
                    if join_count == 0:
                        row[1] = "bez bodu"
                    elif join_count == 1:
                        row[1] = "v pořádku"
                    else:
                        row[1] = "více bodů"
                    
                    cursor.updateRow(row)
            
            # 4. Finální polygon řešeného území s atributem "bod"
            final_polygon_name = f"{out_prefix}Resene_uzemi_Polygon_with_Points" if out_prefix else "Resene_uzemi_Polygon_with_Points"
            final_polygon_fc = os.path.join(output_workspace, generate_unique_fc_name(final_polygon_name, root_gdb))
            
            arcpy.AddMessage(f"[process_resene_point_with_line] 4. Vytvářím finální polygon: {final_polygon_fc}")
            arcpy.management.CopyFeatures(temp_join, final_polygon_fc)
            
            # 5. Vyčištění dočasných dat (včetně snapped linie)
            arcpy.Delete_management(temp_polygon)
            arcpy.Delete_management(temp_join)
            arcpy.Delete_management(snapped_line_fc)  # Smazání dočasné snapped linie
            
            # 6. Statistiky
            bod_value = None
            with arcpy.da.SearchCursor(final_polygon_fc, ["bod"]) as cursor:
                for row in cursor:
                    bod_value = row[0]
                    break
            
            arcpy.AddMessage(f"[process_resene_point_with_line] Analýza dokončena:")
            arcpy.AddMessage(f"  - Hodnocení bodu: {bod_value}")
            arcpy.AddMessage(f"  - Finální polygon: {os.path.basename(final_polygon_fc)}")
            
            return final_polygon_fc  # Vrácení polygonu s atributem "bod"
            
        except Exception as e:
            arcpy.AddError(f"[process_resene_point_with_line] Chyba při zpracování: {e}")
            return None


class Toolbox(object):
    def __init__(self):
        self.label = "CAD Import Tools"
        self.alias = "CAD Tools"
        self.tools = [ExportLayer]


class ExportLayer(object):
    def __init__(self):
        self.label = "Import CAD do GIS"
        self.alias = "exportCadLayer"
        self.canRunInBackground = False
        self.parameters = [
            parameter("Input CAD Soubor", "input_cad", "DEFile"),
            parameter("CAD Vrstva(y)", "cad_layers", "GPString", parameterType="Optional", multiValue=True),
            parameter("Output Geodatabáze", "output_gdb", "DEWorkspace"),
            parameter("Output Feature Dataset (Optional)", "output_fd", "GPString", parameterType="Optional"),
            parameter("XY Tolerance (m)", "xy_tolerance", "GPDouble", parameterType="Optional", defaultValue=0.01),
            parameter("XY Resolution (m)", "xy_resolution", "GPDouble", parameterType="Optional", defaultValue=0.001),
            parameter("Output Souřadnicový Systém", "output_sr", "GPSpatialReference", parameterType="Optional"),
            parameter("Geographic Transformation (Optional)", "transformation", "GPString", parameterType="Optional"),
            parameter("Prefix jména výstupu (Optional)", "out_prefix", "GPString", parameterType="Optional"),
        ]

    def getParameterInfo(self):
        self.parameters[0].filter.list = ["dwg", "dxf", "dgn"]
        self.parameters[2].filter.list = ["Local Database", "Remote Database"]
        self.parameters[1].enabled = False
        return self.parameters

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        if parameters[0].altered and parameters[0].value:
            try:
                cad_file_path = parameters[0].valueAsText
                cfile = CadFile(cad_file_path)
                parameters[1].filter.list = cfile.layer_display_names
                parameters[1].enabled = True
                
                # Automatické předvybrání specifických vrstev
                default_layers = {
                    "101110_PL_Resene_uzemi": ["Polyline"],
                    "200000_PL_Cast_uzemi": ["Polyline", "Point"], 
                    "101111_BL_Resene_uzemi": ["Point"],  # Speciální zpracování pro linii
                    "202110_BL_Cast_uzemi_UP": ["Point"],
                    "203110_BL_Cast_uzemi_SB": ["Point"], 
                    "204110_BL_Cast_uzemi_NB": ["Point"],
                    "205110_BL_Cast_uzemi_XB": ["Point"]
                }
                
                # Najít odpovídající vrstvy v CAD souboru
                selected_layers = []
                for display_name in cfile.layer_display_names:
                    # Extrahovat název vrstvy a typ geometrie z display_name
                    if " (" in display_name and display_name.endswith(")"):
                        layer_name = display_name.split(" (")[0]
                        geometry_type = display_name.split(" (")[1].rstrip(")")
                        
                        # Kontrola, zda vrstva odpovídá požadovaným kritériím
                        if layer_name in default_layers:
                            if geometry_type in default_layers[layer_name]:
                                selected_layers.append(display_name)
                
                if selected_layers:
                    parameters[1].values = selected_layers
                    arcpy.AddMessage(f"[updateParameters] Automaticky předvybrané vrstvy: {selected_layers}")
                
                # Automatické nastavení souřadnicového systému z CAD souboru
                if not parameters[6].altered:
                    desc = arcpy.Describe(cad_file_path)
                    if hasattr(desc, "spatialReference") and desc.spatialReference:
                        sr = desc.spatialReference
                        if sr.name != "Unknown":
                            parameters[6].value = sr
            except Exception as e:
                arcpy.AddWarning(f"[updateParameters] Chyba při načítání CAD: {e}")
        
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        arcpy.env.overwriteOutput = True
        
        input_cad = parameters[0].valueAsText
        selected_layers_text = parameters[1].valueAsText
        output_gdb = parameters[2].valueAsText
        fd_name = parameters[3].valueAsText
        xy_tolerance = parameters[4].value if parameters[4].value is not None else 0.01
        xy_resolution = parameters[5].value if parameters[5].value is not None else 0.001
        output_sr = parameters[6].value
        transform_method = parameters[7].valueAsText
        out_prefix = parameters[8].valueAsText or ""

        cad_file_obj = CadFile(input_cad)

        # Vytvoření Feature Datasetu pokud je specifikován
        if fd_name:
            fd_path = os.path.join(output_gdb, fd_name)
            if not arcpy.Exists(fd_path):
                arcpy.AddMessage(f"[execute] Tvořím Feature Dataset '{fd_name}' v: {output_gdb}")
                arcpy.AddMessage(f"[execute] XY Tolerance: {xy_tolerance} m, XY Resolution: {xy_resolution} m")
                try:
                    # Nastavení prostředí před vytvořením Feature Datasetu
                    original_xy_tolerance = arcpy.env.XYTolerance
                    original_xy_resolution = arcpy.env.XYResolution
                    
                    arcpy.env.XYTolerance = f"{xy_tolerance} Meters"
                    arcpy.env.XYResolution = f"{xy_resolution} Meters"
                    
                    if output_sr:
                        arcpy.CreateFeatureDataset_management(
                            out_dataset_path=output_gdb, 
                            out_name=fd_name, 
                            spatial_reference=output_sr
                        )
                    else:
                        arcpy.CreateFeatureDataset_management(output_gdb, fd_name)
                    
                    # Obnovení původních hodnot prostředí
                    if original_xy_tolerance:
                        arcpy.env.XYTolerance = original_xy_tolerance
                    if original_xy_resolution:
                        arcpy.env.XYResolution = original_xy_resolution
                        
                except Exception as e:
                    arcpy.AddWarning(f"[execute] Nepodařilo se vytvořit FD '{fd_name}': {e}")
                    fd_path = output_gdb
            final_workspace = fd_path
        else:
            final_workspace = output_gdb

        # Zpracování vybraných vrstev
        if selected_layers_text:
            selected_layer_list = [x.strip().strip("'").strip('"') for x in selected_layers_text.split(";")]
            arcpy.AddMessage(f"[execute] Exportuji vybrané vrstvy: {selected_layer_list}")
        else:
            selected_layer_list = []

        # Export CAD vrstev
        exported_layers = cad_file_obj.export_layers(
            selected_display_names=selected_layer_list,
            output_workspace=final_workspace,
            spatial_ref=output_sr,
            transform_method=transform_method,
            out_prefix=out_prefix
        )

        if exported_layers:
            arcpy.AddMessage(f"[execute] Úspěšně exportováno {len(exported_layers)} vrstev:")
            for layer in exported_layers:
                arcpy.AddMessage(f"  - {layer}")
        else:
            arcpy.AddWarning("[execute] Žádné vrstvy nebyly exportovány.")
